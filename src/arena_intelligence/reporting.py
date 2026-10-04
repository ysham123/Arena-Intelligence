"""Post-run metrics. Privileged state is never imported by the live reasoner.

The coverage denominator is the planned set of opportunities, not the subset
that happened to return a usable forecast. Brier score is the unnormalised
multiclass sum of squared probability errors (range 0 to 2).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


def _read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number}: malformed JSON") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path.name}:{line_number}: expected a JSON object")
            records.append(item)
    return records


def _state(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("state", record)


def _command(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("command", record.get("assessment_envelope", record))
    return value if isinstance(value, dict) else {}


def _result(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("result", record.get("assessment_result", {}))
    return value if isinstance(value, dict) else {}


def _outcome(state: dict[str, Any], opponent_team: int) -> int:
    nodes = sorted(state.get("nodes", []), key=lambda node: node["id"])
    counts = [
        sum(
            bot["team"] == opponent_team
            and abs(bot["x"] - node["x"]) + abs(bot["y"] - node["y"]) <= 2
            for bot in state.get("bots", [])
        )
        for node in nodes
    ]
    if not counts or max(counts) == 0:
        return 3
    return int(nodes[counts.index(max(counts))]["id"])


def _probabilities(assessment: dict[str, Any], horizon: int) -> list[float] | None:
    forecast = assessment.get("forecast", {})
    if not isinstance(forecast, dict):
        return None
    values = forecast.get("probabilities", [])
    if forecast.get("horizon_tick") != horizon or not isinstance(values, list) or len(values) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        return None
    if abs(sum(values) - 1.0) > 1e-6:
        return None
    return [float(value) for value in values]


def _cycle_cost(cycle: dict[str, Any]) -> float:
    if "cost_usd" in cycle:
        return float(cycle["cost_usd"])
    usage = cycle.get("usage", [])
    if isinstance(usage, dict):
        usage = [usage]
    return sum(float(item.get("cost_usd", item.get("cost", 0))) for item in usage)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": mean(values),
        "median": median(values),
        "p95": ordered[max(0, math.ceil(0.95 * len(values)) - 1)],
        "max": max(values),
    }


def _latencies(cycles: list[dict[str, Any]]) -> list[float]:
    # A busy planning opportunity is skipped; it is not a zero-duration inference.
    return [
        float(cycle["latency_seconds"])
        for cycle in cycles
        if cycle.get("type") == "reasoning_cycle"
        and cycle.get("status") != "busy"
        and not isinstance(cycle.get("latency_seconds"), bool)
        and isinstance(cycle.get("latency_seconds"), (float, int))
        and math.isfinite(cycle["latency_seconds"])
        and cycle["latency_seconds"] >= 0
    ]


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """Score a recorded match without API calls or modifying its records."""
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / "manifest.json")
    if not manifest:
        raise ValueError(f"Missing manifest.json in {run_dir}")
    config = manifest.get("config", {})
    hz = int(config.get("tick_hz", 10))
    team = int(config.get("reasoner_team", 0))
    duration = int(config.get("duration_ticks", 1800))
    planning_ticks = sorted(
        set(
            int(tick)
            for tick in config.get("planning_ticks", [0, 300, 600, 900, 1200, 1500])
            if 0 <= int(tick) <= duration
        )
    )
    states = {
        _state(record)["tick"]: _state(record) for record in _read_ndjson(run_dir / "states.ndjson")
    }
    observations = _read_ndjson(run_dir / "observations.ndjson")
    observation_by_tick = {
        item["cutoff_tick"]: item for item in observations if item.get("type") == "observation"
    }
    controls = [
        record
        for record in _read_ndjson(run_dir / "commands.ndjson")
        if _command(record).get("type") in {"assessment", "fallback"}
    ]
    commands = [record for record in controls if _command(record).get("type") == "assessment"]
    fallbacks = [record for record in controls if _command(record).get("type") == "fallback"]
    cycles = [
        item
        for item in _read_ndjson(run_dir / "reasoning.ndjson")
        if item.get("type") == "reasoning_cycle"
    ]
    cycles_by_request = {item.get("request_id"): item for item in cycles}
    results = _read_json(run_dir / "results.json", {})
    final_state = states[max(states)] if states else {}
    scores = results.get("scores", final_state.get("scores", [0, 0]))
    outcome = (
        "draw" if scores[0] == scores[1] else ("win" if scores[team] > scores[1 - team] else "loss")
    )
    accepted = [record for record in commands if _result(record).get("accepted") is True]
    accepted.sort(key=lambda record: _result(record).get("application_tick", math.inf))
    accepted_controls = [record for record in controls if _result(record).get("accepted") is True]
    accepted_controls.sort(key=lambda record: _result(record).get("application_tick", math.inf))
    statuses: Counter[str] = Counter()
    forecasts: list[dict[str, Any]] = []
    application_latencies = []
    for cutoff in planning_ticks:
        observation = observation_by_tick.get(cutoff, {})
        request_id = observation.get("request_id")
        candidates = [
            record
            for record in commands
            if _command(record).get("cutoff_tick") == cutoff
            and (request_id is None or _command(record).get("request_id") == request_id)
        ]
        record = next(
            (item for item in candidates if _result(item).get("accepted") is True),
            candidates[0] if candidates else None,
        )
        item: dict[str, Any] = {
            "cutoff_tick": cutoff,
            "horizon_tick": cutoff + 300,
            "request_id": request_id,
            "status": "missing",
            "reason": "no assessment returned",
        }
        cycle = cycles_by_request.get(request_id, {})
        item["reasoning_status"] = cycle.get("status")
        item["latency_seconds"] = cycle.get("latency_seconds")
        fallback = next(
            (
                record
                for record in fallbacks
                if _command(record).get("cutoff_tick") == cutoff
                and (request_id is None or _command(record).get("request_id") == request_id)
            ),
            None,
        )
        if fallback:
            item["fallback"] = {
                "accepted": _result(fallback).get("accepted", False),
                "receipt_tick": fallback.get("receipt_tick"),
                "application_tick": _result(fallback).get("application_tick"),
                "reason": _command(fallback).get("reason", _result(fallback).get("reason")),
            }
        if record is not None:
            envelope, result = _command(record), _result(record)
            request_id = envelope.get("request_id")
            item.update(
                request_id=request_id,
                receipt_tick=record.get("receipt_tick"),
                application_tick=result.get("application_tick"),
                remaining_horizon_ticks=result.get("remaining_horizon_ticks"),
            )
            assessment = envelope.get("assessment", {})
            if not isinstance(assessment, dict):
                assessment = {}
            probabilities = _probabilities(assessment, cutoff + 300)
            item["probabilities"] = probabilities
            if result.get("accepted") is not True:
                item.update(status="rejected", reason=result.get("reason", "not accepted"))
            elif probabilities is None:
                item.update(
                    status="invalid_forecast", reason="invalid probability vector or horizon"
                )
            elif result.get("application_tick", math.inf) > cutoff + 300:
                item.update(
                    status="not_applied", reason="assignment was not applied by forecast horizon"
                )
            elif envelope.get("expiry_tick", cutoff + 450) <= cutoff + 300:
                item.update(
                    status="expired", reason="assignment expired at or before forecast horizon"
                )
            else:
                application_tick = result.get("application_tick", cutoff)
                application_latencies.append((application_tick - cutoff) / hz)
                newer = next(
                    (
                        other
                        for other in accepted_controls
                        if other is not record
                        and application_tick
                        < _result(other).get("application_tick", math.inf)
                        <= cutoff + 300
                    ),
                    None,
                )
                if newer:
                    item.update(
                        status="superseded",
                        reason=(
                            "nearest-objective fallback applied at or before forecast horizon"
                            if _command(newer).get("type") == "fallback"
                            else "new assignment applied at or before forecast horizon"
                        ),
                        superseded_by=_command(newer).get("request_id"),
                        superseded_tick=_result(newer).get("application_tick"),
                        superseded_by_type=_command(newer).get("type"),
                    )
                elif cutoff + 300 not in states:
                    item.update(
                        status="horizon_unavailable", reason="no recorded state at forecast horizon"
                    )
                else:
                    truth = _outcome(states[cutoff + 300], 1 - team)
                    prediction = probabilities.index(max(probabilities))
                    brier = sum(
                        (p - int(index == truth)) ** 2 for index, p in enumerate(probabilities)
                    )
                    item.update(
                        status="scored",
                        reason="accepted assignment remained active through horizon",
                        outcome=truth,
                        prediction=prediction,
                        correct=prediction == truth,
                        brier_score=brier,
                    )
                    if not item.get("remaining_horizon_ticks"):
                        item["remaining_horizon_ticks"] = cutoff + 300 - application_tick
        elif cycle:
            item["reason"] = (
                cycle.get("error")
                or f"reasoning cycle {cycle.get('status', 'returned no assessment')}"
            )
        elif fallback:
            item["reason"] = "cycle failed; nearest-objective fallback requested"
        statuses[item["status"]] += 1
        forecasts.append(item)
    scored = [item for item in forecasts if item["status"] == "scored"]
    rejection_reasons = Counter(
        str(_result(record).get("reason", "unspecified"))
        for record in commands
        if _result(record).get("accepted") is not True
    )
    native_evidence_errors = sum(
        count for reason, count in rejection_reasons.items() if "evidence" in reason.lower()
    )
    host_validation_errors = Counter(
        str(cycle.get("validation_category", "unspecified"))
        for cycle in cycles
        if cycle.get("status") == "invalid"
    )
    evidence_errors = native_evidence_errors + host_validation_errors["evidence"]
    stale = sum(
        count
        for reason, count in rejection_reasons.items()
        if any(word in reason.lower() for word in ("late", "stale", "expired", "horizon"))
    )
    backend = next(
        (cycle.get("backend") for cycle in cycles if cycle.get("backend")),
        config.get("backend", "scripted"),
    )
    mode = next(
        (cycle.get("mode") for cycle in cycles if cycle.get("mode")),
        config.get("reasoning_mode", config.get("mode", backend)),
    )
    mode = {"multi": "two", "mock": "scripted", "frequency": "scripted"}.get(mode, mode)
    latencies = _latencies(cycles)
    tokens = Counter()
    uncertain_usage = 0
    for cycle in cycles:
        usage = cycle.get("usage", [])
        if isinstance(usage, dict):
            usage = [usage]
        for record in usage:
            uncertain_usage += record.get("status") == "uncertain"
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                tokens[key] += int(record.get(key, 0))
    return {
        "run_id": manifest.get("run_id"),
        "run_dir": str(run_dir.resolve()),
        "configuration": f"{backend}/{mode}",
        "backend": backend,
        "mode": mode,
        "seed": config.get("seed"),
        "opponent_policy": config.get("opponent_policy"),
        "mirrored": config.get("mirrored", False),
        "reasoner_team": team,
        "scores": scores,
        "outcome": outcome,
        "final_tick": results.get("tick", max(states) if states else None),
        "planning_opportunities": len(planning_ticks),
        "scored_forecasts": len(scored),
        "forecast_coverage": len(scored) / len(planning_ticks) if planning_ticks else None,
        "forecast_accuracy": mean(item["correct"] for item in scored) if scored else None,
        "brier_score": mean(item["brier_score"] for item in scored) if scored else None,
        "forecast_status_counts": dict(statuses),
        "forecasts": forecasts,
        "accepted_assessments": len(accepted),
        "rejected_assessments": sum(rejection_reasons.values()),
        "accepted_fallbacks": sum(_result(record).get("accepted") is True for record in fallbacks),
        "rejected_fallbacks": sum(
            _result(record).get("accepted") is not True for record in fallbacks
        ),
        "rejection_reasons": dict(rejection_reasons),
        "evidence_errors": evidence_errors,
        "native_evidence_errors": native_evidence_errors,
        "host_validation_errors": dict(host_validation_errors),
        "stale_results": stale,
        "stale_result_rate": stale / len(commands) if commands else 0.0,
        "inference_latency_seconds": _distribution(latencies),
        "application_latency_seconds": _distribution(application_latencies),
        "cost_usd": sum(_cycle_cost(cycle) for cycle in cycles),
        "usage": dict(tokens),
        "uncertain_cost_reservations": uncertain_usage,
        "cost_complete": uncertain_usage == 0,
        "measurement_kind": "live_api"
        if backend in {"claude", "anthropic", "single", "multi"}
        else "offline",
    }


def _wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def summarize_tournament(run_dirs: list[Path]) -> dict[str, Any]:
    """Aggregate by backend/mode; forecast metrics remain opportunity weighted."""
    runs = [summarize_run(path) for path in run_dirs]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[run["configuration"]].append(run)
    configurations = {}
    for name, members in sorted(grouped.items()):
        forecasts = [forecast for run in members for forecast in run["forecasts"]]
        scored = [item for item in forecasts if item["status"] == "scored"]
        correct = sum(item["correct"] for item in scored)
        outcomes = Counter(run["outcome"] for run in members)
        command_count = sum(
            run["accepted_assessments"] + run["rejected_assessments"] for run in members
        )
        configurations[name] = {
            "runs": len(members),
            "wins": outcomes["win"],
            "draws": outcomes["draw"],
            "losses": outcomes["loss"],
            "score_mean": mean(run["scores"][run["reasoner_team"]] for run in members),
            "score_difference_mean": mean(
                run["scores"][run["reasoner_team"]] - run["scores"][1 - run["reasoner_team"]]
                for run in members
            ),
            "planning_opportunities": len(forecasts),
            "scored_forecasts": len(scored),
            "forecast_coverage": len(scored) / len(forecasts) if forecasts else None,
            "forecast_accuracy": correct / len(scored) if scored else None,
            "accuracy_wilson_95": _wilson(correct, len(scored)),
            "brier_score": mean(item["brier_score"] for item in scored) if scored else None,
            "forecast_status_counts": dict(Counter(item["status"] for item in forecasts)),
            "evidence_errors": sum(run["evidence_errors"] for run in members),
            "accepted_fallbacks": sum(run["accepted_fallbacks"] for run in members),
            "rejected_fallbacks": sum(run["rejected_fallbacks"] for run in members),
            "stale_results": sum(run["stale_results"] for run in members),
            "stale_result_rate": sum(run["stale_results"] for run in members) / command_count
            if command_count
            else 0.0,
            "inference_latency_seconds": _distribution(
                [
                    latency
                    for run in members
                    for latency in _latencies(
                        _read_ndjson(Path(run["run_dir"]) / "reasoning.ndjson")
                    )
                ]
            ),
            "cost_usd": sum(run["cost_usd"] for run in members),
            "uncertain_cost_reservations": sum(
                run["uncertain_cost_reservations"] for run in members
            ),
            "measurement_kind": "live_api"
            if all(run["measurement_kind"] == "live_api" for run in members)
            else "offline",
            "conditions": [
                {
                    "seed": run["seed"],
                    "opponent_policy": run["opponent_policy"],
                    "mirrored": run["mirrored"],
                }
                for run in members
            ],
        }
    return {"runs": runs, "configurations": configurations}


def _number(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def write_report(run_dirs: list[Path], output_path: Path) -> Path:
    """Write a factual Markdown evaluation report; never infer mock LLM gains."""
    summary = summarize_tournament(run_dirs)
    lines = [
        "# Arena Intelligence evaluation report",
        "",
        f"Recorded matches: **{len(summary['runs'])}**. "
        "Configuration labels identify the backend and reasoning mode.",
        "",
        "Forecast truth is the resource node with the most opposing bots within Manhattan "
        "distance 2 at cutoff +300 ticks. Positive ties choose the lowest node ID; "
        "zero occupancy is `none`.",
        "",
        "Coverage includes every scheduled planning opportunity, including missing, rejected, "
        "superseded, expired, and unavailable forecasts. Accuracy and unnormalised multiclass "
        "Brier score use only covered forecasts; read them alongside coverage.",
        "",
        "| Configuration | Matches | W / D / L | Mean score | Mean margin | Coverage "
        "| Accuracy | Brier | Known cost USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in summary["configurations"].items():
        lines.append(
            f"| {name} | {group['runs']} | {group['wins']} / {group['draws']} / {group['losses']} "
            f"| {_number(group['score_mean'], 1)} | {_number(group['score_difference_mean'], 1)} "
            f"| {group['scored_forecasts']} / {group['planning_opportunities']} "
            f"| {_number(group['forecast_accuracy'])} | {_number(group['brier_score'])} "
            f"| {_number(group['cost_usd'], 4)} |"
        )
    lines.extend(["", "## Coverage, latency, and uncertainty", ""])
    for name, group in summary["configurations"].items():
        interval = group["accuracy_wilson_95"]
        interval_text = "n/a" if interval is None else f"[{interval[0]:.3f}, {interval[1]:.3f}]"
        statuses = (
            ", ".join(
                f"{key}={value}" for key, value in sorted(group["forecast_status_counts"].items())
            )
            or "none"
        )
        latency = group["inference_latency_seconds"]
        lines.extend(
            [
                f"### {name}",
                "",
                f"- Forecast statuses: {statuses}.",
                f"- Accuracy Wilson 95% interval: {interval_text} over "
                f"{group['scored_forecasts']} covered forecasts. This descriptive interval "
                "treats forecasts as independent; repeated conditions and within-match "
                "correlation limit that interpretation.",
                f"- Inference cycles: {latency['count']}; mean / p95 latency: "
                f"{_number(latency['mean'])} / {_number(latency['p95'])} seconds. Native tick "
                "timing is measured separately by `arena-sim --benchmark`.",
                f"- Evidence errors: {group['evidence_errors']}; stale results: "
                f"{group['stale_results']} ({group['stale_result_rate']:.1%} "
                "of returned assessments).",
                f"- Recorded failure fallbacks: {group['accepted_fallbacks']} accepted, "
                f"{group['rejected_fallbacks']} rejected. Fallbacks are not forecasts or "
                "assessment rejection errors.",
                f"- Unresolved API cost reservations: {group['uncertain_cost_reservations']}. "
                "Reported costs are logged settled amounts; unresolved requests are "
                "not confirmed zero-cost calls.",
                f"- Measurement kind: {group['measurement_kind']}.",
                "",
            ]
        )
    if any(group["measurement_kind"] != "live_api" for group in summary["configurations"].values()):
        lines.extend(
            [
                "Offline scripted/mock/replay runs verify integration and scoring. "
                "They do not establish an improvement from LLM reasoning or "
                "adversarial collaboration.",
                "",
            ]
        )
    lines.extend(
        [
            "## Conditions and records",
            "",
            "| Configuration | Seed | Opponent policy | Mirrored | Result "
            "| Scores (team 0 : team 1) |",
            "|---|---:|---|---|---|---:|",
        ]
    )
    for run in summary["runs"]:
        lines.append(
            f"| {run['configuration']} | {run['seed']} | {run['opponent_policy']} "
            f"| {run['mirrored']} | {run['outcome']} | {run['scores'][0]} : {run['scores'][1]} |"
        )
    lines.extend(
        [
            "",
            "Results describe the listed seeds and policies. Compare configurations "
            "on matching conditions and equal token/spending ceilings before making "
            "a reasoning-performance claim.",
            "",
        ]
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
