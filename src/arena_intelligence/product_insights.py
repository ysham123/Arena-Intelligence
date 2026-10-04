"""Explainable checks over the permitted synthetic stream, with no model calls.

These are inspectable signals, not diagnoses or inferred intent. The event IDs
are the complete provenance of each check's observation window.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from statistics import median
from typing import Any


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _entities(event: dict) -> dict[str, dict]:
    values = event.get("payload", {}).get("entities", [])
    if not isinstance(values, list):
        return {}
    return {
        str(entity["id"]): entity
        for entity in values
        if isinstance(entity, dict)
        and "id" in entity
        and _number(entity.get("x"))
        and _number(entity.get("y"))
    }


def _insight(
    kind: str,
    title: str,
    summary: str,
    evidence: list[dict],
    details: dict,
    alternative: str,
    severity: str = "info",
) -> dict:
    ids = list(dict.fromkeys(str(event["id"]) for event in evidence))
    digest = hashlib.sha256((kind + "|" + "|".join(ids)).encode()).hexdigest()[:16]
    return {
        "id": "check-" + digest,
        "kind": kind,
        "origin": "local_check",
        "severity": severity,
        "title": title,
        "summary": summary,
        "source_time": evidence[-1]["source_time"],
        "evidence_ids": ids,
        "details": details,
        "alternative": alternative,
    }


def analyze_session(events: list[dict], *, latest_source_time: float | None = None) -> list[dict]:
    """Return up to six reproducible signals from snapshot events in this window.

    No future events, hidden positions, or policy labels are used. Missing
    entities break stationary continuity. Source gaps are compared with recent
    source intervals, not wall time; they do not prove a network failure.
    """
    snapshots = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("type") == "snapshot"
        and event.get("id")
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("visibility") != "evaluator"
        and _number(event.get("source_time"))
        and (latest_source_time is None or event["source_time"] <= latest_source_time)
    ]
    snapshots.sort(key=lambda event: (event["source_time"], event.get("sequence", 0)))
    # Multiple snapshots at the same source time are revisions, not new samples.
    by_time = {event["source_time"]: event for event in snapshots}
    snapshots = list(by_time.values())
    if not snapshots:
        return []
    checks: list[dict] = []
    latest = snapshots[-1]
    indexed = [_entities(event) for event in snapshots]

    # Limit stationary signals to three entities to keep review focused.
    stationary = []
    for entity_id, entity in indexed[-1].items():
        position = entity["x"], entity["y"]
        world = latest["payload"].get("world") or {}
        nodes = world.get("nodes", []) if isinstance(world, dict) else []
        if entity.get("objective_id") is not None and any(
            node.get("id") == entity["objective_id"] and (node.get("x"), node.get("y")) == position
            for node in nodes
            if isinstance(node, dict)
        ):
            # Holding the explicitly assigned destination is expected behavior.
            continue
        start = len(snapshots) - 1
        while start > 0:
            prior = indexed[start - 1].get(entity_id)
            if prior is None or (prior["x"], prior["y"]) != position:
                break
            # Repeated display of one old report is not another observation.
            newer = indexed[start].get(entity_id, {})
            if "source_time" in prior and prior.get("source_time") == newer.get("source_time"):
                break
            start -= 1
        window = snapshots[start:]
        duration = latest["source_time"] - window[0]["source_time"]
        # Require a real series; a pair of widely separated points is insufficient.
        intervals = [
            b["source_time"] - a["source_time"] for a, b in zip(window, window[1:], strict=False)
        ]
        if len(window) >= 4 and duration >= 10 and max(intervals) <= 5:
            stationary.append((duration, entity_id, entity, window))
    for duration, entity_id, entity, window in sorted(
        stationary, key=lambda item: (-item[0], item[1])
    )[:3]:
        label = str(entity.get("label") or f"Entity {entity_id}")
        checks.append(
            _insight(
                "stationary_entity",
                f"{label} has held the same reported position",
                f"No reported position change across {len(window)} snapshots spanning "
                f"{duration:g} simulated seconds.",
                window,
                {
                    "entity_id": entity_id,
                    "position": [entity["x"], entity["y"]],
                    "duration_seconds": duration,
                    "samples": len(window),
                    "rule": "At least 4 identical samples over 10 seconds; sample gaps at most 5s.",
                },
                "Holding position, a stationary sensor report, and a stalled controller can look "
                "the same here. Check the simulator's expected behavior.",
            )
        )

    # Examine recent gaps after five intervals establish a local baseline.
    for index in range(max(6, len(snapshots) - 12), len(snapshots)):
        prior = snapshots[index - 6 : index]
        intervals = [
            b["source_time"] - a["source_time"] for a, b in zip(prior, prior[1:], strict=False)
        ]
        typical = median(intervals)
        current = snapshots[index]
        gap = current["source_time"] - prior[-1]["source_time"]
        if gap >= 5 and gap >= 3 * typical:
            checks.append(
                _insight(
                    "telemetry_gap",
                    "A longer interval between snapshots",
                    f"The source advanced {gap:g}s between snapshots; the preceding median "
                    f"interval was {typical:g}s.",
                    [*prior, current],
                    {
                        "gap_seconds": gap,
                        "baseline_seconds": typical,
                        "rule": (
                            "Gap at least 5s and at least 3 times the previous 5 intervals' median."
                        ),
                    },
                    "A reporting-rate change or a simulation pause can produce this pattern. "
                    "This source-time check does not establish packet loss.",
                    "warning",
                )
            )

    world = latest["payload"].get("world", {})
    if not isinstance(world, dict):
        world = {}
    width, height = world.get("width"), world.get("height")
    if _number(width) and _number(height) and width > 0 and height > 0:

        def quadrant(entity: dict) -> tuple[int, int]:
            return int(entity["x"] >= width / 2), int(entity["y"] >= height / 2)

        earlier = [
            event
            for event in snapshots
            if event["source_time"] <= latest["source_time"] - 10
            and event["payload"].get("world") == world
        ]
        if earlier:
            baseline = earlier[-1]
            previous = _entities(baseline)
            teams: dict[str, list[dict]] = defaultdict(list)
            for entity in indexed[-1].values():
                if 0 <= entity["x"] <= width and 0 <= entity["y"] <= height:
                    teams[str(entity.get("team", "unassigned"))].append(entity)
            for team, entities in sorted(teams.items()):
                if len(entities) < 4:
                    continue
                cells: dict[tuple[int, int], list[dict]] = defaultdict(list)
                for entity in entities:
                    cells[quadrant(entity)].append(entity)
                cell, members = max(sorted(cells.items()), key=lambda item: len(item[1]))
                # Compare exactly the same entity identities at the earlier time.
                ids = [str(entity["id"]) for entity in entities]
                if not all(entity_id in previous for entity_id in ids):
                    continue
                count_before = sum(quadrant(previous[entity_id]) == cell for entity_id in ids)
                if len(members) / len(entities) >= 0.6 and len(members) - count_before >= 2:
                    checks.append(
                        _insight(
                            "observed_grouping",
                            "The observed group has become more concentrated",
                            f"{len(members)} of {len(entities)} reported entities in group {team} "
                            f"now share grid quadrant ({cell[0]}, {cell[1]}), "
                            f"up from {count_before}.",
                            [baseline, latest],
                            {
                                "group": team,
                                "quadrant": list(cell),
                                "count": len(members),
                                "previous_count": count_before,
                                "observed_total": len(entities),
                                "entity_ids": ids,
                                "rule": (
                                    "At least 60% of 4+ observed entities; increase of 2+ over 10s."
                                ),
                            },
                            "Grid boundaries and a shared destination can explain the grouping. "
                            "Only reported entities are counted; this does not infer intent.",
                        )
                    )
    return sorted(checks, key=lambda check: (-check["source_time"], check["kind"], check["id"]))[:6]
