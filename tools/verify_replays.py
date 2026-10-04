"""Verify every recorded state and input in a set of runs, without network calls."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--engine", type=Path, default=Path("build/release/arena-sim"))
    parser.add_argument("--output", type=Path, default=Path("reports/replay-verification.json"))
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 9))
    return parser.parse_args()


async def main(args: argparse.Namespace) -> None:
    directories = set()
    for path in args.runs:
        if (path / "manifest.json").is_file():
            directories.add(path.resolve())
        else:
            directories.update(p.parent.resolve() for p in path.glob("*/manifest.json"))
    if not directories:
        raise ValueError("no recorded runs found")
    engine = args.engine.resolve()
    gate = asyncio.Semaphore(args.jobs)
    started = time.monotonic()

    async def verify(directory: Path) -> dict:
        async with gate:
            process = await asyncio.create_subprocess_exec(
                str(engine),
                "--replay",
                str(directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            output, error = await process.communicate()
            if process.returncode:
                raise RuntimeError(f"replay failed in {directory}: {error.decode()[:2000]}")
            result = json.loads(output)
            if not result["ok"] or result["network_calls"] != 0:
                raise ValueError(f"replay verification failed: {directory}")
            return {"run_dir": str(directory), **result}

    rows = await asyncio.gather(*(verify(p) for p in sorted(directories)))
    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "run_count": len(rows),
        "states_verified": sum(r["states_verified"] for r in rows),
        "commands_verified": sum(r["commands_verified"] for r in rows),
        "observations_verified": sum(r["observations_verified"] for r in rows),
        "network_calls": sum(r["network_calls"] for r in rows),
        "elapsed_seconds": time.monotonic() - started,
        "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}))


if __name__ == "__main__":
    asyncio.run(main(options()))
