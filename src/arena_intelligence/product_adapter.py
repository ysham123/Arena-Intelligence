"""Safe conversion of native recordings into explicitly scoped product telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from .product_models import TelemetryBatch


def read_ndjson(path: Path, limit=40000, *, live=False):
    """Read committed lines; live tails may contain an unfinished JSON/UTF-8 record."""
    if not path.is_file():
        return []
    result = []
    with path.open("rb") as stream:
        index = 0
        while line := stream.readline(1048577):
            if index >= limit or len(line) > 1048576:
                raise ValueError("recorded sample exceeds bounded import limits")
            index += 1
            if live and not line.endswith(b"\n"):
                # Native writers commit a record with its newline. Retrying this tail on
                # the next poll preserves completed records without accepting partial data.
                break
            if line.strip():
                result.append(json.loads(line))
    return result


def recording_events(directory: Path, *, live=False):
    """Return observer and evaluator frames; never label evaluator data as public."""
    events = []
    manifest = json.loads((directory / "manifest.json").read_text())
    states = read_ndjson(directory / "states.ndjson", live=live)
    issued = read_ndjson(directory / "observations.ndjson", live=live)
    for item in states:
        state = item.get("state", item)
        tick = state["tick"]
        if tick % 5:
            continue
        cutoff = (
            max((item["cutoff_tick"] for item in issued if item["cutoff_tick"] <= tick), default=0)
            / 10
        )
        world = {
            "width": state["size"],
            "height": state["size"],
            "blocked": state["blocked"],
            "nodes": state["nodes"],
        }
        # The observer's own positions and already delivered last_seen reports are public.
        team = manifest["config"]["reasoner_team"]
        own = [
            {
                "id": str(b["id"]),
                "x": b["x"],
                "y": b["y"],
                "team": "friendly",
                "source_time": tick / 10,
                "objective_id": b["objective_id"],
            }
            for b in state["bots"]
            if b["team"] == team
        ]
        reports = [
            {
                "id": str(r["id"]),
                "x": r["x"],
                "y": r["y"],
                "team": "opponent",
                "source_time": r["observed_tick"] / 10,
            }
            for r in state.get("last_seen", [])
        ]
        events.append(
            {
                "source_time": tick / 10,
                "type": "snapshot",
                "payload": {
                    "entities": own + reports,
                    "world": world,
                    "scores": state["scores"],
                    "visibility": "observer",
                    "tick": tick,
                    "agent_cutoff_time": cutoff,
                },
            }
        )
        truth = [
            {
                "id": str(b["id"]),
                "x": b["x"],
                "y": b["y"],
                "team": "friendly" if b["team"] == team else "opponent",
                "source_time": tick / 10,
                "objective_id": b["objective_id"],
            }
            for b in state["bots"]
        ]
        events.append(
            {
                "source_time": tick / 10,
                "type": "snapshot",
                "payload": {
                    "entities": truth,
                    "world": world,
                    "scores": state["scores"],
                    "visibility": "evaluator",
                    "tick": tick,
                },
            }
        )
    seen = set()
    for observation in issued:
        records = []
        for evidence in observation["observation"]["evidence"]:
            if evidence["id"] in seen:
                continue
            seen.add(evidence["id"])
            data = evidence["data"]
            records.append(
                {
                    "id": evidence["id"],
                    "kind": evidence["kind"],
                    "observed_at": evidence["tick"] / 10,
                    "received_at": data.get("delivered_tick", evidence["tick"]) / 10,
                    "summary": (
                        f"Received synthetic {evidence['kind']} report from source tick "
                        f"{evidence['tick']}; counts are observed lower bounds."
                    ),
                    "details": {
                        key: value
                        for key, value in data.items()
                        if key
                        in {"node_id", "friendly_count", "opponent_count", "bot_id", "x", "y"}
                    },
                    **({"entity_id": str(data["bot_id"])} if "bot_id" in data else {}),
                }
            )
        if records:
            events.append(
                {
                    "source_time": observation["cutoff_tick"] / 10,
                    "type": "evidence",
                    "payload": {
                        "records": records,
                        "available_source_time": observation["cutoff_tick"] / 10,
                    },
                }
            )
    cycles = {
        row.get("request_id"): row for row in read_ndjson(directory / "reasoning.ndjson", live=live)
    }
    for row in read_ndjson(directory / "commands.ndjson", live=live):
        command = row["command"]
        if command.get("type") != "assessment":
            continue
        assessment = command["assessment"]
        cycle = cycles.get(command["request_id"], {})
        payload = {
            "status": cycle.get("status", "recorded"),
            "origin": "model" if cycle.get("backend") in {"single", "multi"} else "scripted",
            "request_id": command["request_id"],
            "cutoff_tick": command["cutoff_tick"],
            "summary": assessment["hypotheses"][0]["claim"],
            "hypotheses": assessment["hypotheses"],
            "assignments": assessment["assignments"],
            "forecast": assessment["forecast"],
            "accepted": bool(row["result"].get("accepted")),
            "expiry_source_time": command["expiry_tick"] / 10,
        }
        if row["result"].get("application_tick") is not None:
            payload["application_source_time"] = row["result"]["application_tick"] / 10
        if cycle.get("latency_seconds") is not None:
            payload["latency_seconds"] = cycle["latency_seconds"]
        if cycle.get("cost_usd") is not None:
            payload["cost_usd"] = cycle["cost_usd"]
        events.append(
            {"source_time": row["receipt_tick"] / 10, "type": "assessment", "payload": payload}
        )
    events.sort(
        key=lambda event: (
            event["source_time"],
            {"snapshot": 0, "evidence": 1, "assessment": 2}.get(event["type"], 3),
        )
    )
    return [{"sequence": index, **event} for index, event in enumerate(events)]


def batches(events, external_id, name, completed=True):
    for offset in range(0, len(events), 100):
        status = "completed" if completed and offset + 100 >= len(events) else "running"
        yield TelemetryBatch.model_validate(
            {
                "schema_version": 1,
                "scope": "synthetic_simulation",
                "session": {"external_id": external_id, "name": name, "status": status},
                "events": events[offset : offset + 100],
            }
        )
