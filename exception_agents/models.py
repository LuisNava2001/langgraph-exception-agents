"""Pydantic models for the synthetic domain and for every slice of the shared graph state.

The LangGraph state stores plain JSON-compatible dicts (so checkpoints stay portable); every
node validates what it reads and what it writes against these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["P1", "P2", "P3"]
SlaStatus = Literal["OK", "AT_RISK", "BREACHED"]
Action = Literal[
    "DISPATCH_TECHNICIAN",
    "REASSIGN_TECHNICIAN",
    "WAIT_FOR_PART_WITH_MITIGATION",
    "ESCALATE_TO_SUPERVISOR",
]
Verdict = Literal["APPROVED", "REVISE", "INSUFFICIENT_EVIDENCE", "POLICY_VIOLATION", "HUMAN_REVIEW"]
Outcome = Literal["RESOLVED", "RESOLVED_AFTER_REVIEW", "ESCALATED", "BLOCKED", "REJECTED"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- synthetic domain (one self-contained snapshot per exception case) ---------------------

class ServiceRequest(Strict):
    request_id: str
    created_at: str
    priority: Priority | None
    customer: str
    location_id: str
    equipment_id: str | None
    symptom: str | None
    assigned_technician: str | None
    required_skill: str
    required_parts: list[str] = []
    estimated_cost: float
    estimated_repair_hours: float
    requested_action: str | None = None


class Equipment(Strict):
    equipment_id: str
    category: str
    model: str
    failure_mode: str | None
    hazards: list[str] = []
    cold_chain: bool = False


class ServiceLocation(Strict):
    location_id: str
    name: str
    region: str
    sla_id: str


class Technician(Strict):
    technician_id: str
    name: str
    region: str
    skills: list[str]
    shift_start: str
    shift_end: str
    on_leave: bool = False
    booked_by: str | None = None


class SparePart(Strict):
    part_id: str
    name: str
    in_stock: int
    eta_hours: float


class CompetingRequest(Strict):
    request_id: str
    priority: Priority
    sla_deadline: str
    technician_id: str


class IncidentHistory(Strict):
    incident_id: str
    date: str
    summary: str
    resolution: str


class ReportedFact(Strict):
    source: str
    field: str
    value: str


class ExceptionInfo(Strict):
    type: str
    summary: str
    reported_by: str


class ReviewerScript(Strict):
    decision: Literal["approve", "reject"]
    reviewer_id: str
    note: str


class Simulation(Strict):
    """Demo Mode only: what the scripted reasoner does and how the simulated reviewer answers.

    Agents never read this block except the scripted reasoner (draft faults) and the runner
    (simulated reviewer, simulated node failure). The critic and validator never see it.
    """

    draft_faults: list[list[str]] = []
    fail_node: str | None = None
    reviewer: ReviewerScript | None = None


class ExceptionCase(Strict):
    case_id: str
    split: Literal["dev", "holdout"]
    scenario: str
    now: str
    exception: ExceptionInfo
    service_request: ServiceRequest
    equipment: Equipment | None
    location: ServiceLocation
    technicians: list[Technician]
    parts: list[SparePart] = []
    competing_requests: list[CompetingRequest] = []
    history: list[IncidentHistory] = []
    reported_facts: list[ReportedFact] = []
    simulation: Simulation = Simulation()


# --- state slices ------------------------------------------------------------------------

class IntakeResult(Strict):
    case_id: str
    request_id: str
    incident_type: str
    priority: Priority | None
    status: Literal["READY", "MISSING_DATA"]
    missing_fields: list[str]
    evidence_provided: list[str]
    untrusted_input_flags: list[str]
    query_terms: list[str]


class Hit(Strict):
    chunk_id: str
    doc_id: str
    title: str
    category: str
    snippet: str
    score: float
    flagged: bool = False
    flag_reason: str | None = None


class ResearchResult(Strict):
    query: str
    hits: list[Hit]
    evidence: list[Hit]
    flagged: list[Hit]
    insufficient: bool
    reason: str | None


class TechnicianFact(Strict):
    technician_id: str
    qualified: bool
    certified: bool
    region_match: bool
    available: bool
    booked_by_higher_or_equal: bool
    eligible: bool
    reasons: list[str]


class PartFact(Strict):
    part_id: str
    in_stock: bool
    eta_hours: float
    known: bool


class Option(Strict):
    option_id: str
    action: Action
    technician_id: str | None
    feasible: bool
    sla_outcome: SlaStatus | None
    description: str


class AnalysisResult(Strict):
    sla_id: str
    sla_deadline: str | None
    sla_remaining_hours: float | None
    sla_status: SlaStatus | None
    earliest_completion: str | None
    technicians: list[TechnicianFact]
    eligible_technicians: list[str]
    policy_technician: str | None
    parts: list[PartFact]
    parts_available: bool
    hazards: list[str]
    required_policies: list[str]
    mitigation_required: str | None
    contradictions: list[dict]
    conflicts: list[dict]
    priority_tie: bool
    human_triggers: list[str]
    forbidden_requested_action: str | None
    options: list[Option]
    fact_ids: list[str]


class Claim(Strict):
    text: str
    support: list[str] = Field(default_factory=list)


class Draft(Strict):
    """A recommendation proposal. It is data for the critic and the validator, never a decision."""

    action: Action
    technician_id: str | None
    part_ids: list[str]
    sla_claim: SlaStatus
    expected_completion: str | None
    mitigation: str | None
    policies_applied: list[str]
    citations: list[str]
    claims: list[Claim]
    alternatives: list[str]
    estimated_cost: float
    rationale: str
    draft_index: int = 0
    source: Literal["scripted", "llm"] = "scripted"


class Finding(Strict):
    code: str
    severity: Literal["fix", "human", "block", "evidence"]
    message: str
    suggestion: str | None = None


class Critique(Strict):
    verdict: Verdict
    findings: list[Finding]
    draft_index: int


class ValidationReport(Strict):
    passed: bool
    checks: list[dict]
    failures: list[Finding]
    route: Literal["respond", "revise", "human_review", "block", "escalate"]
    draft_index: int = 0


class HumanDecision(Strict):
    decision: Literal["approve", "reject"]
    reviewer_id: str
    note: str = ""
    simulated: bool = False


class HumanReviewRecord(Strict):
    decision: Literal["approve", "reject"]
    reviewer_id: str
    note: str
    simulated: bool
    triggers: list[str]
    draft_index: int


class FinalResponse(Strict):
    case_id: str
    outcome: Outcome
    summary: str
    recommendation: dict | None
    evidence: list[dict]
    alternatives: list[dict]
    claims: list[Claim]
    citations: list[str]
    confidence: dict
    limitations: list[str]
    escalation: dict | None
    human_review: dict | None
    revisions: int
    explanation: str
