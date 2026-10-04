"""Local evidence checks exclusively over the permitted observation history."""

from __future__ import annotations

from collections import Counter

from .protocol import CheckRequest, ObservationEnvelope


def run_check(check: CheckRequest, history: list[ObservationEnvelope], cutoff: int) -> dict:
    if check.window_start_tick > check.window_end_tick or check.window_end_tick > cutoff:
        raise ValueError("checks must use an ordered window ending at or before the cutoff")
    evidence = {}
    nodes = {}
    for item in history:
        if item.cutoff_tick > cutoff:
            raise ValueError("history contains a future observation")
        nodes.update({n.id: n for n in item.observation.nodes})
        for e in item.observation.evidence:
            if check.window_start_tick <= e.tick <= check.window_end_tick:
                evidence[e.id] = e
    ordered = sorted(evidence.values(), key=lambda e: (e.tick, e.id))
    result = {
        "kind": check.kind,
        "window_start_tick": check.window_start_tick,
        "window_end_tick": check.window_end_tick,
        "evidence_ids": [e.id for e in ordered],
        "limitation": "Received reports only; opponent counts are sensor-visible lower bounds. "
        "Absence is not proof of empty space.",
    }
    if check.kind == "objective_visits":
        counts: Counter = Counter()
        for e in ordered:
            if e.kind == "sighting":
                for node_id, n in nodes.items():
                    if abs(e.data["x"] - n.x) + abs(e.data["y"] - n.y) <= 2:
                        counts[node_id] += 1
        result["observed_report_counts"] = {str(i): counts[i] for i in range(3)}
        result["limitation"] += " Counts are sightings, not distinct visits or bot totals."
    elif check.kind == "occupancy_trends":
        result["samples"] = [
            {"tick": e.tick, "evidence_id": e.id, **e.data}
            for e in ordered
            if e.kind == "node_status"
        ]
    else:
        delays = [
            e.data["delivered_tick"] - e.data["observed_tick"]
            for e in ordered
            if e.kind == "sighting"
        ]
        result["report_count"] = len(delays)
        result["mean_delay_ticks"] = sum(delays) / len(delays) if delays else None
        result["max_delay_ticks"] = max(delays) if delays else None
    return result
