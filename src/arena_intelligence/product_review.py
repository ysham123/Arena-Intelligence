"""Grounded post-run triage and descriptive comparison; no invented readiness score."""

from __future__ import annotations

import math
from collections import Counter
from statistics import mean


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _session(detail):
    return detail.get("session", detail)


def _replay(detail):
    return detail.get("replay") or {}


def _assessment(replay, request_id):
    for entry in replay.get("commands", []):
        command = entry.get("command", entry)
        if command.get("request_id") == request_id and command.get("type") == "assessment":
            return command.get("assessment", {})
    return {}


def _base(detail, key, source_time=0):
    session = _session(detail)
    return {
        "id": f"{session.get('id', 'unknown')}:{key}",
        "session_id": session.get("id"),
        "session_name": session.get("name", "Unnamed session"),
        "source_time": source_time,
        "status": "review",
        "evidence_ids": [],
    }


def build_review(details: list[dict]) -> dict:
    """Prioritize recorded failures, confident misses, and inspectable local signals.

    This view is explicitly post-run evaluation. A recorded outcome can explain a
    missed forecast here, but is never passed back into a reasoning observation.
    Priority follows a documented rule; it is not a learned risk score.
    """
    items = []
    for detail in details:
        session = _session(detail)
        replay = _replay(detail)
        hz = (replay.get("config") or {}).get("tick_hz", 10) or 10
        if session.get("status") in {"failed", "error"}:
            items.append(
                {
                    **_base(detail, "run-failure"),
                    "kind": "run_failure",
                    "priority": "high",
                    "origin": "recorded_outcome",
                    "title": "The session did not finish successfully",
                    "summary": session.get("error") or "The runtime recorded a failed session.",
                    "why_it_matters": (
                        "The run cannot supply a complete outcome or full forecast coverage."
                    ),
                    "next_check": (
                        "Inspect the final lifecycle events and the last accepted input "
                        "before rerunning."
                    ),
                }
            )
        for forecast in replay.get("forecasts", []):
            rid = forecast.get("request_id", "unknown")
            status = forecast.get("status")
            cutoff = forecast.get("cutoff_tick", 0) / hz
            assessment = _assessment(replay, rid)
            hypotheses = assessment.get("hypotheses", [])
            evidence_ids = list(
                dict.fromkeys(
                    str(eid)
                    for hypothesis in hypotheses
                    for eid in hypothesis.get("evidence_ids", [])
                )
            )
            base = {
                **_base(detail, f"forecast-{rid}", cutoff),
                "request_id": rid,
                "evidence_ids": evidence_ids,
                "origin": "recorded_outcome",
                "assessment": assessment,
                "forecast": forecast,
                "alternatives": [
                    h.get("alternative", "") for h in hypotheses if h.get("alternative")
                ],
            }
            if status != "scored":
                items.append(
                    {
                        **base,
                        "kind": "forecast_uncovered",
                        "priority": "medium",
                        "title": "A planning opportunity has no scored forecast",
                        "summary": f"At {cutoff:g}s: "
                        f"{forecast.get('reason') or status or 'outcome unavailable'}.",
                        "why_it_matters": (
                            "Excluding this opportunity would make reported accuracy look "
                            "better than its coverage supports."
                        ),
                        "next_check": (
                            "Inspect rejection, timeout, expiry, or supersession before "
                            "comparing model quality."
                        ),
                    }
                )
                continue
            if forecast.get("correct") is False:
                probabilities = forecast.get("probabilities", [])
                predicted = forecast.get("prediction")
                actual = forecast.get("outcome")
                probability = (
                    probabilities[predicted]
                    if isinstance(predicted, int) and 0 <= predicted < len(probabilities)
                    else None
                )
                if not _number(probability):
                    continue
                confident = probability >= 0.75

                def target(value):
                    return "no occupied node" if value == 3 else f"node {value}"

                horizon = forecast.get("horizon_tick", 0) / hz
                items.append(
                    {
                        **base,
                        "kind": "forecast_miss",
                        "priority": "high" if confident else "info",
                        "title": f"{probability:.0%} forecast missed the observed outcome",
                        "summary": f"The agent favored {target(predicted)} at {horizon:g}s; "
                        f"the recorded evaluator outcome was {target(actual)}.",
                        "why_it_matters": (
                            "The forecast assigned a large probability to an outcome that did "
                            "not occur. "
                            "This is a useful calibration case, not proof that the objective "
                            "assignment caused the miss."
                            if confident
                            else "The top outcome was wrong, but probability was spread "
                            "across alternatives. "
                            "Review it in the context of calibration and coverage."
                        ),
                        "next_check": (
                            "Compare the reports available at the cutoff with the first "
                            "subsequent reports of the change; then inspect the stated "
                            "alternative."
                        ),
                        "evaluation_boundary": (
                            "Post-run evaluator outcome; not available to the agent at "
                            "forecast time."
                        ),
                    }
                )
            horizon = forecast.get("horizon_tick")
            remaining = forecast.get("remaining_horizon_ticks")
            original = horizon - forecast.get("cutoff_tick", 0) if _number(horizon) else None
            if (
                _number(remaining)
                and _number(original)
                and original > 0
                and remaining < original / 2
            ):
                items.append(
                    {
                        **base,
                        "id": base["id"] + ":horizon",
                        "kind": "reduced_horizon",
                        "priority": "medium",
                        "title": "Most of the forecast horizon elapsed before application",
                        "summary": f"{remaining / hz:g}s remained from the original "
                        f"{original / hz:g}s horizon.",
                        "why_it_matters": (
                            "A correct forecast may have less practical value when the "
                            "controller receives it late."
                        ),
                        "next_check": (
                            "Inspect reasoning latency and application timing separately from "
                            "native update performance."
                        ),
                    }
                )
        for check in detail.get("insights", []):
            items.append(
                {
                    **_base(detail, check["id"], check.get("source_time", 0)),
                    "kind": check.get("kind", "local_check"),
                    "priority": "medium" if check.get("severity") == "warning" else "info",
                    "title": check.get("title", "Local check"),
                    "summary": check.get("summary", ""),
                    "evidence_ids": check.get("evidence_ids", []),
                    "origin": "local_check",
                    "why_it_matters": check.get("details", {}).get(
                        "rule", "A recorded signal crossed an explicit review rule."
                    ),
                    "next_check": check.get("alternative", "Inspect the cited source events."),
                    "details": check.get("details", {}),
                }
            )
    order = {"high": 0, "medium": 1, "info": 2}
    items.sort(key=lambda item: (order[item["priority"]], -item["source_time"], item["id"]))
    counts = Counter(item["priority"] for item in items)
    return {
        "items": items[:200],
        "counts": {level: counts[level] for level in order},
        "sessions_reviewed": len(details),
        "total_items": len(items),
        "truncated": len(items) > 200,
        "method": (
            "Explicit review rules: failed run or missed forecast at >=75% is "
            "high; uncovered forecast, reduced horizon, or cadence warning is "
            "medium; other checks are informational. No release approval is "
            "inferred."
        ),
    }


def _metrics(detail):
    replay = _replay(detail)
    summary = {**(detail.get("summary") or {}), **(replay.get("summary") or {})}
    latencies = [
        f["latency_seconds"]
        for f in replay.get("forecasts", [])
        if _number(f.get("latency_seconds"))
    ]
    frames = replay.get("states") or replay.get("frames") or []
    last = frames[-1] if frames else {}
    scores = last.get("scores", summary.get("scores", []))
    team = (replay.get("config") or {}).get("reasoner_team", 0)
    return {
        "coverage": summary.get("forecast_coverage"),
        "accuracy": summary.get("forecast_accuracy"),
        "brier": summary.get("brier_score"),
        "opportunities": summary.get("planning_opportunities"),
        "scored": summary.get("scored_forecasts"),
        "latency": mean(latencies) if latencies else None,
        "score": scores[team] if isinstance(team, int) and 0 <= team < len(scores) else None,
        "cost": summary.get("cost_usd"),
    }


def compare_sessions(left: dict, right: dict) -> dict:
    """Compare measurements without treating an unmatched pair as an experiment."""
    left_session, right_session = _session(left), _session(right)
    lm, rm = _metrics(left), _metrics(right)
    contexts = [(_replay(d).get("comparison_context") or {}) for d in (left, right)]
    fingerprints = [
        context.get("scenario_fingerprint")
        if isinstance(context, dict)
        else context
        if isinstance(context, str) and len(context) == 64
        else None
        for context in contexts
    ]
    comparable = bool(fingerprints[0] and fingerprints[0] == fingerprints[1])
    same = left_session.get("id") == right_session.get("id")
    if same:
        comparable = False
    definitions = (
        ("score", "Final friendly score", "points", False),
        ("coverage", "Forecast coverage", "%", False),
        ("accuracy", "Forecast accuracy", "%", False),
        ("brier", "Brier score", "", True),
        ("scored", "Scored forecasts", "forecasts", None),
        ("opportunities", "Planning opportunities", "opportunities", None),
        ("latency", "Mean reasoning latency", "s", True),
        ("cost", "Recorded inference cost", "USD", True),
    )
    rows = []
    for key, label, unit, lower in definitions:
        values = [m[key] if _number(m[key]) else None for m in (lm, rm)]
        if unit == "%":
            values = [value * 100 if value is not None else None for value in values]
        rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "lower_is_better": lower,
                "left": values[0],
                "right": values[1],
                "delta": values[1] - values[0] if all(v is not None for v in values) else None,
            }
        )
    limitations = [
        (
            "Two sessions are descriptive evidence, not a statistically "
            "established regression or release decision."
        ),
        (
            "Different assignments can change later observations, even under "
            "the same scenario. Forecast differences are not a matched-input "
            "comparison."
        ),
        "Missing metrics mean not recorded or not applicable; they are not zeros.",
    ]
    if not comparable:
        limitations.insert(
            0,
            "Select two different sessions."
            if same
            else "A matching scenario fingerprint is unavailable; conditions may differ.",
        )
    return {
        "left": {"id": left_session.get("id"), "name": left_session.get("name"), "metrics": lm},
        "right": {"id": right_session.get("id"), "name": right_session.get("name"), "metrics": rm},
        "comparable": comparable,
        "limitations": limitations,
        "rows": rows,
        "verdict": {
            "title": "Same scenario, exploratory comparison"
            if comparable
            else "Descriptive comparison only",
            "detail": (
                "Compare outcomes, coverage, latency, and cost together. An "
                "accuracy gain with lower coverage does not establish a better "
                "system."
            ),
        },
    }
