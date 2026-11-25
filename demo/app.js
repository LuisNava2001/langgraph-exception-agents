/* Renders demo/data.js (window.AGENT_DEMO), produced by `python -m exception_agents demo`. No network access. */
(function () {
  "use strict";
  var data = window.AGENT_DEMO;
  var OUTCOME_TONE = { RESOLVED: "good", RESOLVED_AFTER_REVIEW: "good", ESCALATED: "warn", REJECTED: "warn", BLOCKED: "bad" };
  var VERDICT_TONE = { APPROVED: "good", REVISE: "warn", HUMAN_REVIEW: "sim", INSUFFICIENT_EVIDENCE: "warn", POLICY_VIOLATION: "bad" };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") { node.textContent = attrs[k]; } else { node.setAttribute(k, attrs[k]); }
    });
    (children || []).forEach(function (c) { if (c) { node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); } });
    return node;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } return node; }
  function badge(text, tone) { return el("span", { "class": "badge " + (tone || ""), text: text }); }
  function pct(v) { return v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%"; }

  if (!data) {
    document.getElementById("mode-note").textContent = "demo/data.js is missing. Run `python -m exception_agents demo`.";
    return;
  }

  document.getElementById("mode-note").textContent = data.mode + " Company: " + data.company + ".";
  var badges = document.getElementById("badges");
  badges.appendChild(badge("Demo Mode: no language model", "sim"));
  Object.keys(data.versions).sort().forEach(function (k) { badges.appendChild(badge(k + " " + data.versions[k])); });
  badges.appendChild(badge("LLM Mode: not tested", "warn"));

  // --- evaluation tables -----------------------------------------------------------------------
  var metricNames = Object.keys(data.evaluation.dev.metrics);
  var metrics = document.getElementById("metrics");
  metrics.appendChild(el("thead", {}, [el("tr", {}, [el("th", { text: "Metric" }), el("th", { text: "Gate" }),
    el("th", { text: "dev (" + data.evaluation.dev.counts.cases + " cases)" }),
    el("th", { text: "holdout (" + data.evaluation.holdout.counts.cases + " cases)" })])]));
  var tbody = el("tbody");
  metricNames.forEach(function (name) {
    var gate = data.gates[name];
    var fmt = function (v) { return typeof v === "number" && name !== "unsupported_claims" && name !== "critic_false_alarms" ? pct(v) : String(v); };
    tbody.appendChild(el("tr", {}, [el("td", {}, [el("code", { text: name })]),
      el("td", { "class": "num", text: gate !== undefined ? "≥ " + pct(gate) : (name === "unsupported_claims" ? "= 0" : "—") }),
      el("td", { "class": "num", text: fmt(data.evaluation.dev.metrics[name]) }),
      el("td", { "class": "num", text: fmt(data.evaluation.holdout.metrics[name]) })]));
  });
  metrics.appendChild(tbody);

  var controls = document.getElementById("controls");
  controls.appendChild(el("thead", {}, [el("tr", {}, ["Control", "Result", "Failed gates", "End-to-end", "Policy compliance", "Critic detection"].map(function (h) { return el("th", { text: h }); }))]));
  var cbody = el("tbody");
  data.controls.forEach(function (c) {
    cbody.appendChild(el("tr", {}, [el("td", {}, [el("code", { text: c.control })]),
      el("td", {}, [el("span", { "class": c.passed ? "pass" : "fail", text: c.passed ? "gates passed" : "gates failed" })]),
      el("td", { text: c.failed_gates.join(", ") || "none" }),
      el("td", { "class": "num", text: pct(c.metrics.end_to_end_accuracy) }),
      el("td", { "class": "num", text: pct(c.metrics.policy_compliance) }),
      el("td", { "class": "num", text: pct(c.metrics.critic_detection) })]));
  });
  controls.appendChild(cbody);

  var mutations = document.getElementById("mutations");
  data.mutation_self_test.forEach(function (m) {
    mutations.appendChild(el("li", {}, [el("span", { "class": m.detected ? "pass" : "fail", text: m.detected ? "detected " : "MISSED " }),
      el("code", { text: m.mutation }), " · " + m.detail]));
  });

  // --- filters ---------------------------------------------------------------------------------
  var requested = decodeURIComponent((window.location.hash || "").slice(1));
  var state = { split: "all", outcome: "all", scenario: "all", human: "all",
    selected: data.cases.some(function (c) { return c.case_id === requested; }) ? requested : null };
  function options(values) { return ["all"].concat(values.filter(function (v, i, a) { return a.indexOf(v) === i; }).sort()); }
  var filterSpec = [
    ["split", "Split", options(data.cases.map(function (c) { return c.split; }))],
    ["outcome", "Outcome", options(data.cases.map(function (c) { return c.final.outcome; }))],
    ["scenario", "Scenario", options(data.cases.map(function (c) { return c.scenario; }))],
    ["human", "Human review", ["all", "simulated reviewer", "none"]]
  ];
  var filters = document.getElementById("filters");
  filterSpec.forEach(function (spec) {
    var select = el("select", { id: "f-" + spec[0] }, spec[2].map(function (v) { return el("option", { value: v, text: v }); }));
    select.addEventListener("change", function () { state[spec[0]] = select.value; renderList(); });
    filters.appendChild(el("label", {}, [spec[1], select]));
  });

  function visible() {
    return data.cases.filter(function (c) {
      return (state.split === "all" || c.split === state.split) && (state.outcome === "all" || c.final.outcome === state.outcome)
        && (state.scenario === "all" || c.scenario === state.scenario)
        && (state.human === "all" || (state.human === "none") === !c.human_review);
    });
  }

  function renderList() {
    var list = clear(document.getElementById("case-list"));
    var items = visible();
    if (!items.length) { list.appendChild(el("p", { "class": "hint", text: " No case matches." })); clear(document.getElementById("case-detail")); return; }
    if (!items.some(function (c) { return c.case_id === state.selected; })) { state.selected = items[0].case_id; }
    items.forEach(function (c) {
      var ok = Object.keys(c.evaluation.checks).every(function (k) { return c.evaluation.checks[k]; });
      var b = el("button", { type: "button", "aria-current": c.case_id === state.selected ? "true" : "false" }, [
        el("span", { "class": "cid", text: c.case_id }), " ", badge(c.final.outcome, OUTCOME_TONE[c.final.outcome]),
        el("span", { "class": "meta", text: c.scenario + (c.human_review ? " · simulated reviewer" : "") + (ok ? "" : " · evaluation mismatch") })]);
      b.addEventListener("click", function () { state.selected = c.case_id; renderList(); });
      list.appendChild(b);
    });
    renderDetail(data.cases.filter(function (c) { return c.case_id === state.selected; })[0]);
  }

  function kv(pairs) {
    var dl = el("dl", { "class": "kv" });
    pairs.forEach(function (p) { dl.appendChild(el("dt", { text: p[0] })); dl.appendChild(el("dd", { text: p[1] === null || p[1] === undefined ? "—" : String(p[1]) })); });
    return dl;
  }

  function renderDetail(c) {
    var d = clear(document.getElementById("case-detail"));
    var req = c.incident.request;
    d.appendChild(el("h3", {}, [c.case_id + " · " + c.scenario + " ", badge(c.final.outcome, OUTCOME_TONE[c.final.outcome]),
      " ", badge("expected " + c.evaluation.expected_outcome)]));
    d.appendChild(el("h4", { text: "Original incident" }));
    d.appendChild(el("p", { text: c.incident.type + ": " + c.incident.summary }));
    d.appendChild(kv([["request", req.request_id + " · " + (req.priority || "no priority") + " · cost " + req.estimated_cost],
      ["equipment", c.incident.equipment ? c.incident.equipment.category + " (" + c.incident.equipment.failure_mode + ")" : "missing"],
      ["location", c.incident.location.name + " · " + c.incident.location.region + " · " + c.incident.location.sla_id],
      ["assigned technician", req.assigned_technician], ["requested action", req.requested_action],
      ["reported facts", c.incident.reported_facts.map(function (f) { return f.source + ": " + f.field + "=" + f.value; }).join("; ") || "—"]]));
    if (c.intake) {
      d.appendChild(el("h4", { text: "Initial state (intake)" }));
      d.appendChild(kv([["status", c.intake.status], ["missing fields", c.intake.missing_fields.join(", ") || "none"],
        ["untrusted input flags", c.intake.untrusted_input_flags.join(", ") || "none"]]));
    }
    d.appendChild(el("h4", { text: "Path through the graph" }));
    d.appendChild(el("ol", { "class": "timeline" }, c.trace.map(function (t) {
      return el("li", { "class": t.status === "error" ? "error" : "" }, [el("span", { text: String(t.step) }),
        el("span", { "class": "node", text: t.node }), el("span", { "class": "sum", text: t.summary })]);
    })));
    if (c.research) {
      d.appendChild(el("h4", { text: "Retrieved evidence (BM25, lexical)" }));
      d.appendChild(el("p", { "class": "hint", text: c.research.insufficient ? "Insufficient: " + c.research.reason : "Query: " + c.research.query }));
      c.research.hits.forEach(function (h) {
        var tags = [badge(h.doc_id), " ", el("span", { text: h.title + " · score " + h.score })];
        if (h.flagged) { tags.push(" "); tags.push(badge("quarantined: " + h.flag_reason, "bad")); }
        else if (c.research.evidence.indexOf(h.chunk_id) >= 0) { tags.push(" "); tags.push(badge("citable", "good")); }
        else { tags.push(" "); tags.push(badge("not used")); }
        d.appendChild(el("div", { "class": "panel" + (h.flagged ? " flagged" : "") }, [el("div", { "class": "head" }, tags),
          el("span", { text: h.snippet })]));
      });
    }
    if (c.drafts.length) {
      d.appendChild(el("h4", { text: "Drafts and critic review" }));
      c.drafts.forEach(function (dr, i) {
        var crit = c.critiques[i];
        var head = [badge("draft " + dr.draft_index + (i === 0 ? " (initial)" : " (revision)")), " ",
          el("code", { text: dr.action + " " + (dr.technician_id || "") + " · SLA " + dr.sla_claim + " · cites " + dr.citations.join(", ") })];
        if (crit) { head.push(" "); head.push(badge(crit.verdict, VERDICT_TONE[crit.verdict])); }
        var body = [el("div", { "class": "head" }, head)];
        if (crit && crit.findings.length) {
          body.push(el("ul", {}, crit.findings.map(function (f) {
            return el("li", {}, [el("code", { text: f.code }), " " + f.message + (f.suggestion ? " → " + f.suggestion : "")]);
          })));
        }
        d.appendChild(el("div", { "class": "panel" }, body));
      });
    }
    if (c.validations.length) {
      d.appendChild(el("h4", { text: "Deterministic validation gate" }));
      c.validations.forEach(function (v) {
        d.appendChild(el("div", { "class": "panel" }, [el("div", { "class": "head" }, [badge("draft " + v.draft_index), " ",
          badge(v.passed ? "all checks passed" : "route: " + v.route, v.passed ? "good" : "warn")]),
          el("span", { text: v.checks.map(function (k) { return (k.passed ? "✓ " : "✗ ") + k.check_id; }).join("   ") })]));
      });
    }
    if (c.human_review) {
      d.appendChild(el("h4", { text: "Human review" }));
      var hr = c.human_review;
      d.appendChild(el("div", { "class": "panel" }, [el("div", { "class": "head" }, [badge(hr.simulated ? "simulated reviewer" : "reviewer", "sim"), " ",
        badge(hr.decision, hr.decision === "approve" ? "good" : "warn")]),
        el("span", { text: hr.reviewer_id + " · triggers " + hr.triggers.join(", ") + (hr.note ? " · \"" + hr.note + "\"" : "") })]));
    }
    if (c.errors.length) {
      d.appendChild(el("h4", { text: "Agent errors" }));
      c.errors.forEach(function (e) { d.appendChild(el("div", { "class": "panel flagged", text: e.node + ": " + e.type + ": " + e.message })); });
    }
    d.appendChild(el("h4", { text: "Final decision" }));
    d.appendChild(el("pre", { "class": "explanation", text: c.final.explanation }));
    d.appendChild(el("h4", { text: "Evaluation of this case" }));
    d.appendChild(el("ul", { "class": "checks" }, Object.keys(c.evaluation.checks).map(function (k) {
      var okk = c.evaluation.checks[k];
      return el("li", {}, [el("span", { "class": okk ? "pass" : "fail", text: okk ? "pass " : "FAIL " }), el("code", { text: k })]);
    })));
  }

  renderList();
})();
