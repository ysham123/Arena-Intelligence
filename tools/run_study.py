"""Run frozen conditions concurrently, with one reasoning cycle per match.

Each match remains paced at 10Hz. All requests share the same transactional
ledger. API latency in the report is measured under this recorded concurrency.
Run with: uv run --env-file .env python tools/run_study.py --jobs 6
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from arena_intelligence.budget import CostLedger
from arena_intelligence.protocol import RunConfig
from arena_intelligence.reasoner import ClaudeProvider, Reasoner, reasoning_config
from arena_intelligence.reporting import summarize_tournament, write_report
from arena_intelligence.runner import run_match


def options() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix", type=Path, default=Path("configs/heldout-claude.json"))
    p.add_argument("--engine", type=Path, default=Path("build/release/arena-sim"))
    p.add_argument("--run-root", type=Path, default=Path("runs/heldout-claude"))
    p.add_argument("--output", type=Path, default=Path("reports/heldout-claude.json"))
    p.add_argument("--ledger", type=Path, default=Path(".arena/costs.sqlite3"))
    p.add_argument("--jobs", type=int, default=4, choices=range(1, 9))
    p.add_argument("--allow-paid", action="store_true")
    return p.parse_args()


async def main(args: argparse.Namespace) -> None:
    matrix_bytes = args.matrix.read_bytes()
    matrix = json.loads(matrix_bytes)
    backends = matrix["backends"]
    scenarios = matrix["scenarios"]
    if not scenarios or len(scenarios) * len(backends) > 256:
        raise ValueError("matrix must contain 1..256 runs")
    if any(b not in {"single", "multi", "mock", "frequency"} for b in backends):
        raise ValueError("unknown backend")
    paid = any(b in {"single", "multi"} for b in backends)
    if paid and not args.allow_paid:
        raise ValueError("paid matrices require --allow-paid")
    ledger = CostLedger(args.ledger) if paid else None
    provider = ClaudeProvider(ledger, "eval") if ledger else None
    gate = asyncio.Semaphore(args.jobs)
    completed = 0
    started = time.monotonic()
    directories: list[Path] = []
    tasks: list[tuple[int, str, RunConfig, Path]] = []
    for index, scenario in enumerate(scenarios):
        config = RunConfig.model_validate({**scenario, "paced": paid})
        for backend in backends:
            directory = args.run_root / f"scenario-{index:03d}-{backend}"
            if (directory / "manifest.json").exists():
                raise ValueError(f"run already exists: {directory}; choose a fresh run root")
            directories.append(directory)
            tasks.append((index, backend, config, directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "conditions_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
        "model_configurations": {backend: reasoning_config(backend) for backend in backends},
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                list(Path("src/arena_intelligence").glob("*.py"))
                + list(Path("native/src").glob("*.cpp"))
                + list(Path("native/include/arena").glob("*.hpp"))
            )
        },
        "parallel_matches": args.jobs,
        "one_cycle_inflight_per_match": True,
        "paced": paid,
        "total_runs": len(tasks),
        "observations": "closed-loop: each controller affects its later sensor coverage",
        "budget_before": ledger.summary() if ledger else None,
    }
    metadata_path = args.output.with_name(args.output.stem + "-metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    async def run(item: tuple[int, str, RunConfig, Path]) -> None:
        nonlocal completed
        index, backend, config, directory = item
        async with gate:
            result = await run_match(args.engine, directory, config, Reasoner(backend, provider))
            completed += 1
            progress = {
                "completed": completed,
                "total": len(tasks),
                "condition_index": index,
                "backend": backend,
                "terminal": result["terminal"]["type"],
                "elapsed_seconds": time.monotonic() - started,
                "budget": ledger.summary() if ledger else None,
            }
            args.output.with_name(args.output.stem + "-progress.json").write_text(
                json.dumps(progress, indent=2) + "\n"
            )
            print(json.dumps(progress), flush=True)

    try:
        await asyncio.gather(*(run(item) for item in tasks))
        metadata["inference_completed_at_utc"] = datetime.now(UTC).isoformat()
        metadata["budget_after_inference"] = ledger.summary() if ledger else None
    finally:
        if provider:
            await provider.close()
    summary = summarize_tournament(directories)
    args.output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    write_report(directories, args.output.with_suffix(".md"))
    metadata["completed_at_utc"] = datetime.now(UTC).isoformat()
    metadata["elapsed_seconds"] = time.monotonic() - started
    metadata["budget_after"] = ledger.summary() if ledger else None
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"report": str(args.output.resolve()), "metadata": metadata}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main(options()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
