"""Forecast comparison on identical permitted observation streams.

Paid default: frequency, single, and multi; explicit --allow-paid is required. Each
reference stream delivers six recorded snapshots 30 seconds apart. The
reference's hidden state is read only after every model cycle has finished.
--offline exercises the pipeline with mock/frequency without network calls.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from arena_intelligence.budget import CostLedger
from arena_intelligence.protocol import AssessmentEnvelope, ObservationEnvelope, validate_assessment
from arena_intelligence.reasoner import MODEL, ClaudeProvider, Reasoner, reasoning_config
from arena_intelligence.reporting import _outcome

CUTOFFS = (0, 300, 600, 900, 1200, 1500)
DEADLINE_SECONDS = 20.0
DELIVERY_SECONDS = 30.0
PAID_BACKENDS = ("frequency", "single", "multi")
ASSIGNMENT_ASSUMPTION = (
    "Forecasts are conditional on their proposed assignments, which are not applied in this "
    "matched-input benchmark. The three current scripted opponent policies and overlapping "
    "bots make opponent trajectories independent of friendly assignments; a native regression "
    "test checks this invariance. Labels use the recorded opponent trajectories. Score and "
    "W/D/L belong to the separate closed-loop study."
)


@dataclass(frozen=True)
class Reference:
    directory: Path
    observations: tuple[ObservationEnvelope, ...]
    source_sha256: str


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_hashes() -> dict[str, str]:
    """Identify the evaluation and inference source used for this measurement."""
    root = Path(__file__).resolve().parents[1]
    files = (
        "tools/compare_observations.py",
        "src/arena_intelligence/reasoner.py",
        "src/arena_intelligence/context.py",
        "src/arena_intelligence/evidence.py",
        "src/arena_intelligence/protocol.py",
        "src/arena_intelligence/budget.py",
        "src/arena_intelligence/reporting.py",
    )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def load_reference(directory: Path) -> Reference:
    """Read the public stream only; never open manifest, results, or states here."""
    path = Path(directory).resolve()
    raw = (path / "observations.ndjson").read_bytes()
    observations = tuple(
        ObservationEnvelope.model_validate_json(line) for line in raw.splitlines() if line.strip()
    )
    if tuple(item.cutoff_tick for item in observations) != CUTOFFS:
        raise ValueError("reference must contain exactly the six standard planning cutoffs")
    if len({item.run_id for item in observations}) != 1:
        raise ValueError("reference observations must belong to one run")
    for name in ("manifest.json", "results.json", "states.ndjson"):
        if not (path / name).is_file():
            raise ValueError(f"reference is missing {name}")
    return Reference(path, observations, hashlib.sha256(raw).hexdigest())


def permitted_ids(history: list[ObservationEnvelope]) -> set[str]:
    return {
        evidence_id
        for item in history
        for evidence_id in (
            [e.id for e in item.observation.evidence]
            + [e.evidence_id for e in item.observation.last_seen]
            + [e.evidence_id for e in item.observation.visible_opponents]
        )
    }


def append_record(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        output.flush()


async def run_reference(
    reference: Reference,
    directory: Path,
    reasoners: dict[str, Any],
    *,
    paced: bool = True,
    sleep=asyncio.sleep,
    clock=time.monotonic,
) -> None:
    """At most one cycle per configuration; histories are independently deep copied."""
    if directory.exists():
        raise ValueError("matched output already exists; choose a fresh run root")
    directory.mkdir(parents=True)
    for backend in reasoners:
        (directory / backend).mkdir()
    origin = clock()
    for index, request in enumerate(reference.observations):
        scheduled = index * DELIVERY_SECONDS
        if paced:
            await sleep(max(0.0, origin + scheduled - clock()))
        history = list(reference.observations[: index + 1])
        fingerprint = canonical_hash([item.model_dump() for item in history])
        known = permitted_ids(history)

        async def execute(
            backend: str,
            reasoner: Any,
            *,
            request=request,
            history=history,
            known=known,
            index=index,
            fingerprint=fingerprint,
            scheduled=scheduled,
        ) -> None:
            log_path = directory / backend / "matched_cycles.ndjson"
            common = {
                "schema_version": 1,
                "backend": backend,
                "reference_run_id": request.run_id,
                "request_id": request.request_id,
                "cutoff_tick": request.cutoff_tick,
                "opportunity_index": index,
                "input_history_sha256": fingerprint,
                "source_observations_sha256": reference.source_sha256,
                "scheduled_delivery_seconds": scheduled,
                "delivery_seconds": clock() - origin,
                "deadline_seconds": DEADLINE_SECONDS,
                "validation_scope": "typed schema, permitted evidence, legal assignments",
                "native_acceptance": "not evaluated",
                "assignments_applied": False,
                "assignment_assumption": ASSIGNMENT_ASSUMPTION,
            }
            append_record(log_path, {"type": "matched_cycle_start", **common})
            started = time.monotonic()
            try:
                reasoning = await reasoner.cycle(
                    request.model_copy(deep=True),
                    [item.model_copy(deep=True) for item in history],
                    set(known),
                    deadline_seconds=DEADLINE_SECONDS,
                )
            except Exception as error:
                reasoning = {
                    "status": "worker_error",
                    "error": type(error).__name__,
                    "assessment": None,
                    "usage": [],
                    "latency_seconds": time.monotonic() - started,
                    "cost_usd": 0,
                }
            status = reasoning.get("status", "invalid")
            assessment = None
            category = reasoning.get("validation_category")
            if status == "valid":
                try:
                    typed = AssessmentEnvelope.model_validate(reasoning["assessment"])
                    validated = validate_assessment(typed.assessment, request, known)
                    if typed != validated:
                        raise ValueError("assessment envelope does not match the issued context")
                    assessment = typed.model_dump()
                    if reasoning.get("latency_seconds", math.inf) > DEADLINE_SECONDS:
                        status = "late"
                except Exception as error:
                    status = "invalid"
                    category = getattr(error, "category", "schema_or_provider")
            append_record(
                log_path,
                {
                    "type": "matched_cycle",
                    **common,
                    "status": status,
                    "structurally_validated": assessment is not None,
                    "validation_category": category,
                    "assessment": assessment,
                    "reasoning": reasoning,
                },
            )

        # All configurations receive this exact source history before the next delivery.
        async with asyncio.TaskGroup() as group:
            for backend, reasoner in reasoners.items():
                group.create_task(execute(backend, reasoner))


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": mean(values) if values else None,
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
    }


def load_truth(reference: Reference) -> tuple[dict, dict[int, int], str]:
    """Post-inference evaluator: only this function opens privileged records."""
    manifest = json.loads((reference.directory / "manifest.json").read_text())
    result = json.loads((reference.directory / "results.json").read_text())
    config = manifest["config"]
    if manifest["run_id"] != reference.observations[0].run_id:
        raise ValueError("reference manifest/run ID mismatch")
    if manifest["engine_version"] != "0.1.0" or config["opponent_policy"] not in {
        "nearest",
        "holder",
        "switch",
    }:
        raise ValueError("reference lacks the supported opponent-trajectory invariance")
    if result["status"] != "completed" or result["tick"] != 1800:
        raise ValueError("reference must be a completed 1800-tick match")
    horizons = {cutoff + 300 for cutoff in CUTOFFS}
    labels = {}
    digest = hashlib.sha256()
    with (reference.directory / "states.ndjson").open("rb") as stream:
        for line in stream:
            digest.update(line)
            if not line.strip():
                continue
            record = json.loads(line)
            state = record.get("state", record)
            tick = state["tick"]
            if tick in horizons:
                labels[tick] = _outcome(state, 1 - reference.observations[0].observation.team)
    return config, labels, digest.hexdigest()


def read_cycles(path: Path) -> dict[int, dict]:
    if not path.is_file():
        return {}
    records = {}
    with path.open() as stream:
        for line in stream:
            value = json.loads(line)
            if value.get("type") == "matched_cycle":
                if value["cutoff_tick"] in records:
                    raise ValueError("duplicate completed matched cycle")
                records[value["cutoff_tick"]] = value
    return records


def evaluate_matched(references: list[Reference], run_root: Path, backends: list[str]) -> dict:
    """Call only after all model tasks have finished or been cancelled and joined."""
    rows = []
    conditions = []
    for reference in references:
        config, labels, state_hash = load_truth(reference)
        conditions.append({k: config[k] for k in ("seed", "opponent_policy", "mirrored")})
        for backend in backends:
            cycles = read_cycles(
                run_root / reference.directory.name / backend / "matched_cycles.ndjson"
            )
            for index, request in enumerate(reference.observations):
                cycle = cycles.get(request.cutoff_tick, {})
                expected_hash = canonical_hash(
                    [item.model_dump() for item in reference.observations[: index + 1]]
                )
                if cycle and cycle["input_history_sha256"] != expected_hash:
                    raise ValueError("matched configuration received different public history")
                reasoning = cycle.get("reasoning", {})
                status = cycle.get("status", "missing")
                row = {
                    "reference_run_id": request.run_id,
                    "reference_directory": str(reference.directory),
                    "backend": backend,
                    "request_id": request.request_id,
                    "cutoff_tick": request.cutoff_tick,
                    "horizon_tick": request.cutoff_tick + 300,
                    "input_history_sha256": expected_hash,
                    "truth_states_sha256": state_hash,
                    "status": status,
                    "structurally_validated": cycle.get("structurally_validated", False),
                    "native_acceptance": "not evaluated",
                    "latency_seconds": reasoning.get("latency_seconds"),
                    "cost_usd": reasoning.get("cost_usd", 0),
                    "validation_category": cycle.get("validation_category"),
                    "error": reasoning.get("error"),
                    "usage": reasoning.get("usage", []),
                }
                if status == "valid":
                    if row["horizon_tick"] not in labels:
                        row["status"] = "horizon_unavailable"
                    else:
                        probabilities = cycle["assessment"]["assessment"]["forecast"][
                            "probabilities"
                        ]
                        truth = labels[row["horizon_tick"]]
                        prediction = probabilities.index(max(probabilities))
                        row.update(
                            status="scored",
                            probabilities=probabilities,
                            outcome=truth,
                            prediction=prediction,
                            correct=prediction == truth,
                            brier_score=sum(
                                (p - int(i == truth)) ** 2 for i, p in enumerate(probabilities)
                            ),
                        )
                rows.append(row)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["backend"]].append(row)
    summaries = {}
    for backend, members in grouped.items():
        scored = [item for item in members if item["status"] == "scored"]
        usage = [u for item in members for u in item["usage"]]
        uncertain = sum(u.get("status") == "uncertain" for u in usage)
        summaries[backend] = {
            "planning_opportunities": len(members),
            "scored_forecasts": len(scored),
            "forecast_coverage": len(scored) / len(members),
            "forecast_accuracy": mean(item["correct"] for item in scored) if scored else None,
            "brier_score": mean(item["brier_score"] for item in scored) if scored else None,
            "status_counts": dict(Counter(item["status"] for item in members)),
            "evidence_errors": sum(item["validation_category"] == "evidence" for item in members),
            "latency_seconds": distribution(
                [item["latency_seconds"] for item in members if item["latency_seconds"] is not None]
            ),
            "known_cost_usd": sum(item["cost_usd"] or 0 for item in members),
            "uncertain_cost_reservations": uncertain,
            "cost_complete": uncertain == 0,
            "usage": {
                key: sum(u.get(key, 0) for u in usage) for key in ("input_tokens", "output_tokens")
            },
            "model_config": reasoning_config(backend),
        }
    seeds = {condition["seed"] for condition in conditions}
    condition_set = {(c["seed"], c["opponent_policy"], c["mirrored"]) for c in conditions}
    full = (
        len(seeds) == 4
        and len(condition_set) == 24
        and condition_set
        == {
            (seed, policy, mirrored)
            for seed in seeds
            for policy in ("nearest", "holder", "switch")
            for mirrored in (False, True)
        }
    )
    return {
        "benchmark": "matched_permitted_observation_forecasts",
        "assignment_assumption": ASSIGNMENT_ASSUMPTION,
        "native_acceptance": "not evaluated; predictions are structurally validated",
        "conditions": conditions,
        "full_4_seed_3_policy_2_side_matrix": full,
        "configurations": summaries,
        "forecasts": rows,
    }


def write_summary(summary: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    lines = [
        "# Matched observation forecast benchmark",
        "",
        ASSIGNMENT_ASSUMPTION,
        "",
        "All configurations receive identical permitted histories at the six planning cutoffs. "
        "Coverage includes failed, late, invalid, and missing outputs. Accuracy and multiclass "
        "Brier use covered forecasts; read them alongside coverage.",
        "",
        "| Configuration | Covered / opportunities | Accuracy | Brier | "
        "Mean latency s | Known cost USD |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def number(value):
        return "n/a" if value is None else f"{value:.4f}"

    for backend, data in sorted(summary["configurations"].items()):
        lines.append(
            f"| {backend} | {data['scored_forecasts']} / {data['planning_opportunities']} | "
            f"{number(data['forecast_accuracy'])} | {number(data['brier_score'])} | "
            f"{number(data['latency_seconds']['mean'])} | {number(data['known_cost_usd'])} |"
        )
    lines.extend(
        [
            "",
            "Native acceptance and score/WDL are not measured here. Free scripted runs "
            "exercise this benchmark and do not establish an LLM reasoning gain. Latencies include "
            "the recorded concurrent load; repeated forecasts within a reference are correlated.",
            "",
        ]
    )
    output.with_suffix(".md").write_text("\n".join(lines))


async def check_model_access(provider: ClaudeProvider) -> dict:
    try:
        model = await provider.client.models.retrieve(MODEL)
    except Exception as error:
        raise RuntimeError(f"model access check failed: {type(error).__name__}") from None
    return {"requested_model": MODEL, "accessible_model_id": model.id}


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=Path("runs/heldout-final"))
    parser.add_argument("--references", nargs="+", type=Path)
    parser.add_argument(
        "--run-root", "--output-root", type=Path, default=Path("runs/matched-final")
    )
    parser.add_argument("--output", type=Path, default=Path("reports/matched-final.json"))
    parser.add_argument("--ledger", type=Path, default=Path(".arena/costs.sqlite3"))
    parser.add_argument("--jobs", type=int, default=6, choices=range(1, 7))
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


async def main(args):
    if not args.offline and not args.allow_paid:
        raise ValueError("single/multi API benchmark requires explicit --allow-paid")
    paths = args.references or sorted(args.reference_root.glob("scenario-*-mock"))
    if not paths or len(paths) > 256 or len({p.name for p in paths}) != len(paths):
        raise ValueError("provide 1..256 distinct reference directories")
    references = [load_reference(path) for path in paths]
    if args.run_root.exists():
        raise ValueError("matched run root already exists; choose a fresh destination")
    backends = ["mock", "frequency"] if args.offline else list(PAID_BACKENDS)
    ledger = None if args.offline else CostLedger(args.ledger)
    provider = None if args.offline else ClaudeProvider(ledger, "eval")
    metadata = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "reference_streams": len(references),
        "backends": backends,
        "parallel_reference_streams": args.jobs,
        "maximum_concurrent_configuration_cycles": args.jobs * len(backends),
        "maximum_concurrent_paid_cycles": 0 if args.offline else args.jobs * 2,
        "source_sha256": source_hashes(),
        "one_cycle_inflight_per_configuration": True,
        "delivery_interval_seconds": DELIVERY_SECONDS if not args.offline else None,
        "deadline_seconds": DEADLINE_SECONDS,
        "paced": not args.offline,
        "measurement_kind": "offline" if args.offline else "live_api",
        "references": [
            {"directory": str(r.directory), "source_sha256": r.source_sha256} for r in references
        ],
        "model_configurations": {b: reasoning_config(b) for b in backends},
        "budget_before": ledger.summary() if ledger else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = args.output.with_name(args.output.stem + "-metadata.json")
    started = time.monotonic()
    try:
        if provider:
            metadata["model_access"] = await check_model_access(provider)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        gate = asyncio.Semaphore(args.jobs)

        async def run(reference):
            async with gate:
                reasoners = {backend: Reasoner(backend, provider) for backend in backends}
                await run_reference(
                    reference,
                    args.run_root / reference.directory.name,
                    reasoners,
                    paced=not args.offline,
                )
                print(json.dumps({"completed_reference": reference.directory.name}), flush=True)

        async with asyncio.TaskGroup() as group:
            for reference in references:
                group.create_task(run(reference))
    finally:
        if provider:
            await provider.close()
    # The TaskGroup has joined every inference task before privileged evaluator reads.
    summary = evaluate_matched(references, args.run_root, backends)
    metadata.update(
        completed_at_utc=datetime.now(UTC).isoformat(),
        elapsed_seconds=time.monotonic() - started,
        budget_after=ledger.summary() if ledger else None,
    )
    summary["metadata"] = metadata
    write_summary(summary, args.output)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"report": str(args.output.resolve())}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main(options()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
