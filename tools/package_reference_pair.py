"""Build the installed product's paired reference assets from saved native records.

This reads a verified release archive and makes zero simulation/model calls.
Original historical recordings are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

from arena_intelligence.product_adapter import recording_events
from arena_intelligence.product_models import TelemetryEvent
from arena_intelligence.reporting import summarize_run
from arena_intelligence.viewer import generate_viewer

FILES = (
    "manifest.json",
    "states.ndjson",
    "commands.ndjson",
    "observations.ndjson",
    "reasoning.ndjson",
    "results.json",
    "run_summary.json",
)


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def build_pair(archive: Path, destination: Path, scenario: int = 4) -> list[dict]:
    if not 0 <= scenario <= 999:
        raise ValueError("scenario must be 0..999")
    destination.mkdir(parents=True, exist_ok=True)
    receipts = []
    comparison = None
    with tempfile.TemporaryDirectory(prefix="arena-reference-pair-") as temporary:
        temporary = Path(temporary)
        with zipfile.ZipFile(archive) as source:
            for backend in ("single", "multi"):
                name = f"scenario-{scenario:03d}-{backend}"
                directory = temporary / name
                directory.mkdir()
                hashes = {}
                for filename in FILES:
                    member = f"arena-intelligence/runs/heldout-claude/{name}/{filename}"
                    data = source.read(member)
                    (directory / filename).write_bytes(data)
                    hashes[filename] = {
                        "archive_member": member,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                manifest = json.loads((directory / "manifest.json").read_text())
                history = json.loads((directory / "run_summary.json").read_text())
                if history["backend"] != backend:
                    raise ValueError("reference backend does not match its historical summary")
                context = {
                    k: v for k, v in manifest["config"].items() if k not in {"paced", "backend"}
                }
                fingerprint = canonical_hash(context)
                if comparison is not None and comparison != fingerprint:
                    raise ValueError("reference pair has different simulation conditions")
                comparison = fingerprint
                events = recording_events(directory)
                # Reindex/chunk evidence without changing any recorded content or timing.
                bounded = []
                for event in events:
                    if event["type"] == "evidence" and len(event["payload"]["records"]) > 128:
                        records = event["payload"]["records"]
                        for offset in range(0, len(records), 128):
                            bounded.append(
                                {
                                    **event,
                                    "payload": {
                                        **event["payload"],
                                        "records": records[offset : offset + 128],
                                    },
                                }
                            )
                    else:
                        bounded.append(event)
                canonical = [
                    TelemetryEvent.model_validate({**event, "sequence": index}).model_dump(
                        exclude_none=True
                    )
                    for index, event in enumerate(bounded)
                ]
                if not canonical or any(
                    a["source_time"] > b["source_time"]
                    for a, b in zip(canonical, canonical[1:], strict=False)
                ):
                    raise ValueError("reference events must be nonempty and time ordered")
                # Use the same viewer metadata as the audited standalone artifact.
                html = generate_viewer(directory, temporary / f"{name}.html").read_text()
                match = re.search(
                    r'<script id="replay-data" type="application/json">(.*?)</script>',
                    html,
                    re.DOTALL,
                )
                if not match:
                    raise ValueError("standalone viewer has no replay metadata")
                replay = json.loads(match.group(1))
                for key in ("states", "map", "observations"):
                    replay.pop(key, None)
                summary = summarize_run(directory)
                replay["config"] = manifest["config"]
                duration_seconds = (
                    manifest["config"]["duration_ticks"] / manifest["config"]["tick_hz"]
                )
                replay["comparison_context"] = {
                    "scenario_fingerprint": fingerprint,
                    "scenario_label": (
                        f"Seed {manifest['config']['seed']} · "
                        f"{manifest['config']['bots_per_team']} bots/team · "
                        f"{duration_seconds:g} s"
                    ),
                }
                replay["summary"].update(
                    {
                        key: summary[key]
                        for key in (
                            "backend",
                            "mode",
                            "scores",
                            "outcome",
                            "reasoner_team",
                            "inference_latency_seconds",
                            "application_latency_seconds",
                            "evidence_errors",
                            "stale_results",
                            "uncertain_cost_reservations",
                            "cost_complete",
                        )
                    }
                )
                replay["summary"]["final_tick"] = summary["final_tick"]
                asset = {
                    "schema_version": 1,
                    "name": f"Recorded {backend} agent · scenario {scenario:03d}",
                    "source_kind": "recorded_model_reference",
                    "backend": backend,
                    "source_run_id": manifest["run_id"],
                    "engine_version": manifest["engine_version"],
                    "source_checksums": hashes,
                    "events": canonical,
                    "replay": replay,
                }
                encoded = json.dumps(
                    asset, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ).encode()
                if len(encoded) > 12 * 1024 * 1024:
                    raise ValueError("reference asset exceeds 12 MiB")
                output = destination / f"{name}.json"
                staged = output.with_suffix(".tmp")
                staged.write_bytes(encoded)
                staged.replace(output)
                receipts.append(
                    {
                        "file": str(output),
                        "bytes": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "backend": backend,
                        "source_run_id": manifest["run_id"],
                        "comparison_context": fingerprint,
                        "events": len(canonical),
                        "scores": summary["scores"],
                        "forecast_accuracy": summary["forecast_accuracy"],
                        "cost_usd": summary["cost_usd"],
                    }
                )
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=Path("../arena-intelligence-experiments.zip")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("src/arena_intelligence/assets/reference-pair")
    )
    parser.add_argument("--scenario", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(build_pair(args.archive, args.output, args.scenario), indent=2))


if __name__ == "__main__":
    main()
