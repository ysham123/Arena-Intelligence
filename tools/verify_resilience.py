"""Run measured, zero-cost paced latency faults and verify their native replays.

Run from the repository root with: uv run python tools/verify_resilience.py
This takes approximately 25 seconds. It never constructs an API provider.
"""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from arena_intelligence.process import native_command
from arena_intelligence.protocol import RunConfig
from arena_intelligence.reasoner import Reasoner
from arena_intelligence.runner import run_match


async def measure(name: str, delay: float, ticks: int, engine: Path, root: Path) -> dict:
    directory = root / name
    summary = await run_match(
        engine,
        directory,
        RunConfig(seed=42, paced=True, duration_ticks=ticks, planning_ticks=[0]),
        Reasoner("mock", mock_delay_seconds=delay),
        deadline_seconds=20,
    )
    cycles = [
        json.loads(line) for line in (directory / "reasoning.ndjson").read_text().splitlines()
    ]
    states = [json.loads(line) for line in (directory / "states.ndjson").read_text().splitlines()]
    commands = [
        json.loads(line) for line in (directory / "commands.ndjson").read_text().splitlines()
    ]
    assessments = [c for c in commands if c["command"]["type"] == "assessment"]
    fallbacks = [c for c in commands if c["command"]["type"] == "fallback"]
    replay = await native_command(engine, "--replay", str(directory.resolve()))
    measured = {
        "name": name,
        "injected_delay_seconds": delay,
        "deadline_seconds": 20,
        "planned_ticks": ticks,
        "recorded_ticks": states[-1]["tick"],
        "wall_seconds": summary["duration_seconds"],
        "cycle_statuses": [c["status"] for c in cycles],
        "cycle_latency_seconds": [c["latency_seconds"] for c in cycles],
        "accepted": [c["result"]["accepted"] for c in assessments],
        "application_ticks": [
            c["result"]["application_tick"] for c in assessments if c["result"]["accepted"]
        ],
        "fallback_application_ticks": [
            c["result"]["application_tick"] for c in fallbacks if c["result"]["accepted"]
        ],
        "replay": replay,
    }
    assert measured["recorded_ticks"] == ticks, measured
    assert replay["ok"] and replay["network_calls"] == 0, replay
    if delay < 20:
        assert measured["accepted"] == [True], measured
        assert 95 <= measured["application_ticks"][0] <= 110, measured
    else:
        assert measured["cycle_statuses"] == ["timeout"] and not assessments, measured
        assert len(fallbacks) == 1 and fallbacks[0]["result"]["accepted"], measured
    return measured


async def main() -> None:
    root = Path("runs") / ("latency-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    engine = Path(sys.argv[1] if len(sys.argv) > 1 else "build/release/arena-sim").resolve()
    cases = await asyncio.gather(
        measure("delay-10s", 10, 150, engine, root),
        measure("deadline-25s", 25, 250, engine, root),
    )
    report = {
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "api_calls": 0,
        "measurement": "10Hz paced C++ with asynchronous delayed Python mock",
        "cases": cases,
    }
    output = Path("reports/resilience.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
