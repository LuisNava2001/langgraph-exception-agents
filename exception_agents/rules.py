"""Deterministic business facts: SLA, technician eligibility, parts, policies, conflicts.

These functions are the single source of operational truth for the agents. A language model
(LLM Mode) may propose a recommendation, but the facts it is checked against come from here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import Settings, policies
from .models import AnalysisResult, ExceptionCase, Option, PartFact, TechnicianFact

PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def sla_deadline(case: ExceptionCase) -> datetime | None:
    request = case.service_request
    if request.priority is None:
        return None
    agreement = policies()["sla_agreements"][case.location.sla_id]
    return parse_time(request.created_at) + timedelta(hours=agreement["resolution_hours"][request.priority])


def required_certifications(case: ExceptionCase) -> list[str]:
    hazard_policies = policies()["hazard_policies"]
    rules = policies()["policies"]
    hazards = case.equipment.hazards if case.equipment else []
    return sorted(rules[hazard_policies[h]]["certification"] for h in hazards if h in hazard_policies)


def conflict_relation(case: ExceptionCase, competing) -> str:
    """How a competing request relates to this one for the same technician (POL-PRIORITY-CONFLICT)."""
    own_rank = PRIORITY_RANK.get(case.service_request.priority or "P3", 3)
    other_rank = PRIORITY_RANK[competing.priority]
    if other_rank < own_rank:
        return "higher"
    if other_rank > own_rank:
        return "lower"
    own_deadline = sla_deadline(case)
    other_deadline = parse_time(competing.sla_deadline)
    if own_deadline is None or other_deadline < own_deadline:
        return "equal_earlier"
    if other_deadline > own_deadline:
        return "equal_later"
    return "tie"


def technician_facts(case: ExceptionCase) -> tuple[list[TechnicianFact], list[dict]]:
    request = case.service_request
    now = parse_time(case.now)
    certs = required_certifications(case)
    competing = {c.request_id: c for c in case.competing_requests}
    facts, conflicts = [], []
    for tech in case.technicians:
        reasons = []
        qualified = request.required_skill in tech.skills
        certified = all(cert in tech.skills for cert in certs)
        region_match = tech.region == case.location.region
        start = max(now, parse_time(tech.shift_start))
        on_shift = start + timedelta(hours=request.estimated_repair_hours) <= parse_time(tech.shift_end)
        available = not tech.on_leave and on_shift
        blocked = False
        if tech.booked_by and tech.booked_by in competing:
            relation = conflict_relation(case, competing[tech.booked_by])
            conflicts.append({"request_id": tech.booked_by, "technician_id": tech.technician_id, "relation": relation})
            blocked = relation in ("higher", "equal_earlier")
            if blocked:
                reasons.append(f"booked by {tech.booked_by} ({relation} priority)")
            elif relation == "tie":
                reasons.append(f"tied with {tech.booked_by}: same priority and deadline")
        if not qualified:
            reasons.append(f"lacks skill {request.required_skill}")
        if not certified:
            reasons.append("missing certification " + ", ".join(c for c in certs if c not in tech.skills))
        if not region_match:
            reasons.append(f"works in {tech.region}, not {case.location.region}")
        if tech.on_leave:
            reasons.append("on leave")
        elif not on_shift:
            reasons.append("shift does not cover the repair window")
        eligible = qualified and certified and region_match and available and not blocked
        facts.append(TechnicianFact(technician_id=tech.technician_id, qualified=qualified, certified=certified,
                                    region_match=region_match, available=available,
                                    booked_by_higher_or_equal=blocked, eligible=eligible, reasons=reasons))
    return facts, conflicts


def part_facts(case: ExceptionCase) -> list[PartFact]:
    stock = {p.part_id: p for p in case.parts}
    facts = []
    for part_id in case.service_request.required_parts:
        part = stock.get(part_id)
        facts.append(PartFact(part_id=part_id, in_stock=bool(part and part.in_stock > 0),
                              eta_hours=part.eta_hours if part else 0.0, known=part is not None))
    return facts


def contradictions(case: ExceptionCase) -> list[dict]:
    by_field: dict[str, list] = {}
    for fact in case.reported_facts:
        by_field.setdefault(fact.field, []).append({"source": fact.source, "value": fact.value})
    return [{"field": field, "reports": reports} for field, reports in sorted(by_field.items())
            if len({r["value"].strip().lower() for r in reports}) > 1]


def compute_facts(case: ExceptionCase, settings: Settings) -> AnalysisResult:
    rules = policies()
    request, equipment = case.service_request, case.equipment
    now = parse_time(case.now)
    deadline = sla_deadline(case)
    techs, conflicts = technician_facts(case)
    eligible = [t.technician_id for t in techs if t.eligible]
    roster = {t.technician_id: t for t in case.technicians}
    eligible.sort(key=lambda tid: (max(now, parse_time(roster[tid].shift_start)), tid))
    parts = part_facts(case)
    parts_available = all(p.in_stock for p in parts)
    tied = {c["technician_id"] for c in conflicts if c["relation"] == "tie"}
    if request.assigned_technician in eligible and request.assigned_technician not in tied:
        chosen = request.assigned_technician
    else:  # prefer an eligible technician without a tie; a tie is left for a human (POL-PRIORITY-CONFLICT)
        chosen = next((t for t in eligible if t not in tied), eligible[0] if eligible else None)

    completion = None
    if chosen:
        start = max(now, parse_time(roster[chosen].shift_start))
        waiting = max([p.eta_hours for p in parts if not p.in_stock] or [0.0])
        completion = max(start, now + timedelta(hours=waiting)) + timedelta(hours=request.estimated_repair_hours)
    if deadline is None:
        status = None
    elif now > deadline:
        status = "BREACHED"
    elif completion is None or completion > deadline:
        status = "AT_RISK"
    else:
        status = "OK"

    hazards = list(equipment.hazards) if equipment else []
    required = {rules["hazard_policies"][h] for h in hazards if h in rules["hazard_policies"]}
    mitigation = None
    if equipment and equipment.cold_chain and equipment.failure_mode in rules["cold_chain_failure_modes"]:
        required.add("POL-FOOD-SAFETY")
        mitigation = rules["policies"]["POL-FOOD-SAFETY"]["mitigation"]
    if status == "BREACHED":
        required.add("POL-SLA-BREACH")
    found_contradictions = contradictions(case)
    if found_contradictions:
        required.add("POL-CONTRADICTION")
    if conflicts:
        required.add("POL-PRIORITY-CONFLICT")
    triggers = []
    if request.estimated_cost > settings.cost_approval_threshold:
        required.add("POL-COST-APPROVAL")
        triggers.append("COST_APPROVAL_REQUIRED")
    if found_contradictions:
        triggers.append("CONTRADICTION")
    priority_tie = chosen in tied
    if priority_tie:
        triggers.append("PRIORITY_TIE")
    forbidden = request.requested_action if request.requested_action in rules["policies"]["POL-FORBIDDEN-ACTIONS"]["actions"] else None

    options = [
        Option(option_id="OPT-DISPATCH", action="DISPATCH_TECHNICIAN", technician_id=request.assigned_technician,
               feasible=request.assigned_technician in eligible and parts_available,
               sla_outcome=status if request.assigned_technician in eligible else None,
               description="Dispatch the originally assigned technician."),
        Option(option_id="OPT-REASSIGN", action="REASSIGN_TECHNICIAN",
               technician_id=next((t for t in eligible if t != request.assigned_technician), None),
               feasible=any(t != request.assigned_technician for t in eligible) and parts_available,
               sla_outcome=status, description="Reassign to another eligible technician."),
        Option(option_id="OPT-WAIT-MITIGATE", action="WAIT_FOR_PART_WITH_MITIGATION", technician_id=chosen,
               feasible=not parts_available and chosen is not None, sla_outcome=status,
               description="Expedite the missing part, schedule the repair on arrival and apply the mitigation."),
        Option(option_id="OPT-ESCALATE", action="ESCALATE_TO_SUPERVISOR", technician_id=None, feasible=True,
               sla_outcome=None, description="Hand the case to a dispatch supervisor."),
    ]
    fact_ids = ["fact:request", "fact:sla"] + [f"fact:technician:{t.technician_id}" for t in case.technicians] \
        + [f"fact:part:{p.part_id}" for p in case.parts] + [f"fact:policy:{p}" for p in sorted(required)]
    return AnalysisResult(
        sla_id=case.location.sla_id, sla_deadline=iso(deadline),
        sla_remaining_hours=round((deadline - now).total_seconds() / 3600, 2) if deadline else None,
        sla_status=status, earliest_completion=iso(completion), technicians=techs, eligible_technicians=eligible,
        policy_technician=chosen,
        parts=parts, parts_available=parts_available, hazards=hazards, required_policies=sorted(required),
        mitigation_required=mitigation, contradictions=found_contradictions, conflicts=conflicts,
        priority_tie=priority_tie, human_triggers=triggers, forbidden_requested_action=forbidden,
        options=options, fact_ids=fact_ids,
    )
