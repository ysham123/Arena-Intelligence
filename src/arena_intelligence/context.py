"""Deterministic bounded model context built exclusively from public observations."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .evidence import run_check
from .protocol import CheckRequest, Evidence, ObservationEnvelope

CONTEXT_VERSION = "compact-v1"
MAX_LATEST_SIGHTINGS = 16
MAX_EVIDENCE_RECORDS = 32
MAX_CHECK_CITATIONS = 8
MAX_PROMPT_BYTES = 32_000
CHECK_KINDS = ("objective_visits", "occupancy_trends", "report_timing")


def permitted_records(history: list[ObservationEnvelope], cutoff: int) -> list[Evidence]:
    records = {}
    for snapshot in history:
        if snapshot.cutoff_tick > cutoff:
            raise ValueError("context cannot include a future observation")
        for record in snapshot.observation.evidence:
            records[record.id] = record
    return sorted(records.values(), key=lambda e: (e.tick, e.id))


def _public_record(record: Evidence) -> dict:
    fields = (
        ("bot_id", "x", "y")
        if record.kind == "sighting"
        else ("node_id", "friendly_count", "opponent_count")
    )
    return {
        "evidence_id": record.id,
        "kind": record.kind,
        "observed_tick": record.data["observed_tick"],
        "delivered_tick": record.data["delivered_tick"],
        **{field: record.data[field] for field in fields},
    }


def _spaced(items: list[Any], limit: int) -> list[Any]:
    """Retain endpoints and evenly spaced observations, without random sampling."""
    if len(items) <= limit:
        return items
    if limit == 1:
        return items[-1:]
    return [items[i * (len(items) - 1) // (limit - 1)] for i in range(limit)]


def compact_check_summary(result: dict, records: list[Evidence]) -> dict:
    """Summarize full-history checks without sending their entire evidence arrays."""
    window = [
        e for e in records if result["window_start_tick"] <= e.tick <= result["window_end_tick"]
    ]
    relevant_kind = "node_status" if result["kind"] == "occupancy_trends" else "sighting"
    relevant = [e for e in window if e.kind == relevant_kind]
    sampled = _spaced(relevant, MAX_CHECK_CITATIONS)
    summary = {
        "kind": result["kind"],
        "window_start_tick": result["window_start_tick"],
        "window_end_tick": result["window_end_tick"],
        "limitation": result["limitation"],
        "coverage": {
            "unique_received_records": len(window),
            "relevant_received_records": len(relevant),
            "cited_records": len(sampled),
            "counts_use_all_relevant_received_records": True,
        },
        "evidence_ids": [e.id for e in sampled],
    }
    if result["kind"] == "objective_visits":
        summary["observed_report_counts"] = result["observed_report_counts"]
    elif result["kind"] == "report_timing":
        for field in ("report_count", "mean_delay_ticks", "max_delay_ticks"):
            summary[field] = result[field]
    else:
        groups: dict[int, list[Evidence]] = defaultdict(list)
        for record in relevant:
            groups[record.data["node_id"]].append(record)
        summary["nodes"] = [
            {
                "node_id": node_id,
                "received_report_count": len(group),
                "minimum_observed_opponents": min(e.data["opponent_count"] for e in group),
                "maximum_observed_opponents": max(e.data["opponent_count"] for e in group),
                "first": _public_record(group[0]),
                "latest": _public_record(group[-1]),
            }
            for node_id, group in sorted(groups.items())
        ]
    return summary


def initial_check_summaries(
    history: list[ObservationEnvelope], cutoff: int, records: list[Evidence]
) -> list[dict]:
    return [
        compact_check_summary(
            run_check(
                CheckRequest(kind=kind, window_start_tick=0, window_end_tick=cutoff),
                history,
                cutoff,
            ),
            records,
        )
        for kind in CHECK_KINDS
    ]


def compact_context(
    request: ObservationEnvelope, history: list[ObservationEnvelope], records: list[Evidence]
) -> dict:
    """One map, one current state, and an explicitly incomplete received-report sample."""
    obs = request.observation
    latest: dict[int, dict] = {}
    for record in records:
        if record.kind == "sighting":
            latest[record.data["bot_id"]] = _public_record(record)
    by_id = {e.id: e for e in records}
    # Last-seen references can outlive the native rolling evidence array. Their
    # public source timestamp/ID remain usable; delivery time may be unavailable.
    for report in obs.last_seen + obs.visible_opponents:
        previous = latest.get(report.id)
        if previous is None or report.observed_tick > previous["observed_tick"]:
            source = by_id.get(report.evidence_id)
            latest[report.id] = {
                "evidence_id": report.evidence_id,
                "kind": "sighting",
                "bot_id": report.id,
                "x": report.x,
                "y": report.y,
                "observed_tick": report.observed_tick,
                "delivered_tick": source.data["delivered_tick"] if source else None,
            }
    latest_reports = sorted(latest.values(), key=lambda r: (-r["observed_tick"], r["bot_id"]))[
        :MAX_LATEST_SIGHTINGS
    ]
    chosen = {report["evidence_id"] for report in latest_reports}
    candidates: list[Evidence] = []
    nodes: dict[int, list[Evidence]] = defaultdict(list)
    bots: dict[int, list[Evidence]] = defaultdict(list)
    for record in records:
        if record.kind == "node_status":
            nodes[record.data["node_id"]].append(record)
        else:
            bots[record.data["bot_id"]].append(record)
    # Objective histories retain their first, middle, and latest received samples.
    for _, group in sorted(nodes.items()):
        candidates.extend(_spaced(group, 3))
    # Earlier sightings preserve some trajectories instead of only the last point.
    for _, group in sorted(bots.items()):
        candidates.extend(_spaced(group, 3))
    candidates = sorted({e.id: e for e in candidates}.values(), key=lambda e: (e.tick, e.id))
    remaining = [e for e in candidates if e.id not in chosen]
    sample = _spaced(remaining, MAX_EVIDENCE_RECORDS - len(latest_reports))
    chosen.update(e.id for e in sample)
    past = {h.cutoff_tick: h for h in history if h.cutoff_tick < request.cutoff_tick}
    return {
        "version": CONTEXT_VERSION,
        "rules": {
            "tick_hz": 10,
            "movement": "Each bot moves one cell every 5 ticks using deterministic terrain "
            "routing toward its assigned objective; multiple bots can share a cell.",
            "scoring": "Once per 10 ticks, each node awards one point to the team with a "
            "strictly larger bot count within Manhattan distance 2; ties award no points.",
            "sensor_range_manhattan": 6,
            "assignment_expiry_tick": request.cutoff_tick + 450,
            "forecast_horizon_tick": request.cutoff_tick + 300,
            "forecast_condition": "Forecast assumes your recommended assignments are accepted; "
            "opponent movement remains uncertain.",
        },
        "arena": {
            "size": obs.size,
            "nodes": [{"id": n.id, "x": n.x, "y": n.y} for n in obs.nodes],
            "blocked": obs.blocked,
        },
        "current": {
            "cutoff_tick": request.cutoff_tick,
            "team": obs.team,
            "scores": obs.scores,
            "friendly": [
                {"id": b.id, "x": b.x, "y": b.y, "objective_id": b.objective_id}
                for b in obs.friendly
            ],
        },
        "latest_received_sightings": latest_reports,
        "representative_evidence": [_public_record(e) for e in sample],
        "past_scores": [
            {"cutoff_tick": tick, "scores": past[tick].observation.scores}
            for tick in sorted(past)[-4:]
        ],
        "coverage": {
            "retained_observation_count": len({h.cutoff_tick for h in history}),
            "oldest_retained_cutoff_tick": min(h.cutoff_tick for h in history),
            "newest_retained_cutoff_tick": request.cutoff_tick,
            "unique_received_evidence_count": len(records),
            "represented_evidence_count": len(chosen),
            "omitted_received_evidence_count": len({e.id for e in records} - chosen),
            "known_opponents_with_received_sightings": len(latest),
            "represented_opponents": len(latest_reports),
            "sampling": "Up to 16 newest per-bot reports, then evenly spaced per-node and "
            "per-bot historical samples; maximum 32 evidence records in this context.",
            "limitation": "This is a sample of retained received observations, not complete "
            "world history. Opponent counts are sensor-visible lower bounds; zero does not "
            "prove empty space. Initial check counts use all retained received records.",
        },
    }


def encode_prompt(payload: dict) -> str:
    prompt = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise ValueError("compact model prompt exceeds 32000-byte limit")
    return prompt
