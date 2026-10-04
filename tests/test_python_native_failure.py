"""End-to-end failure recovery uses the actual native engine and zero network calls."""

import json
import os
from collections import deque
from pathlib import Path

import pytest

from arena_intelligence.process import native_command
from arena_intelligence.protocol import RunConfig
from arena_intelligence.reasoner import Reasoner
from arena_intelligence.runner import run_match

ENGINE = Path(
    os.environ.get("ARENA_ENGINE", Path(__file__).resolve().parents[1] / "build/release/arena-sim")
)


class ExplodingProvider:
    async def produce(self):
        raise RuntimeError("sensitive worker detail must not enter the logs")


class FailingWorker(Reasoner):
    def __init__(self):
        super().__init__("mock")
        self.failing_provider = ExplodingProvider()

    async def cycle(self, request, history, known_evidence, deadline_seconds):
        if request.cutoff_tick > 0:
            await self.failing_provider.produce()
        result = await super().cycle(request, history, known_evidence, deadline_seconds)
        for assignment in result["assessment"]["assessment"]["assignments"]:
            assignment["objective_id"] = 2
        return result


def shortest_distances(state, node):
    blocked = {tuple(cell) for cell in state["blocked"]}
    start = (node["x"], node["y"])
    distances = {start: 0}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for candidate in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]:
            a, b = candidate
            if (
                0 <= a < state["size"]
                and 0 <= b < state["size"]
                and candidate not in blocked
                and candidate not in distances
            ):
                distances[candidate] = distances[(x, y)] + 1
                queue.append(candidate)
    return distances


@pytest.mark.skipif(not ENGINE.exists(), reason="build the native release engine first")
async def test_paced_worker_crash_resets_old_advice_at_next_tick_and_replays(tmp_path):
    run_dir = tmp_path / "failure"
    summary = await run_match(
        ENGINE,
        run_dir,
        RunConfig(duration_ticks=30, planning_ticks=[0, 10], paced=True),
        FailingWorker(),
    )
    assert summary["terminal"]["type"] == "finished"
    assert summary["terminal"]["tick"] == 30
    commands = [json.loads(line) for line in (run_dir / "commands.ndjson").read_text().splitlines()]
    fallback = next(record for record in commands if record["command"]["type"] == "fallback")
    assert fallback["command"]["reason"] == "worker_error"
    assert fallback["result"]["accepted"]
    applied = fallback["result"]["application_tick"]
    assert applied == fallback["receipt_tick"] + 1
    states = {
        record["state"]["tick"]: record["state"]
        for record in map(json.loads, (run_dir / "states.ndjson").read_text().splitlines())
    }
    assert {bot["objective_id"] for bot in states[applied - 1]["bots"] if bot["team"] == 0} == {2}
    state = states[applied]
    distances = {node["id"]: shortest_distances(state, node) for node in state["nodes"]}
    for bot in state["bots"]:
        if bot["team"] == 0:
            expected = min(
                distances, key=lambda n: (distances[n].get((bot["x"], bot["y"]), 9999), n)
            )
            assert bot["objective_id"] == expected
    log = (run_dir / "reasoning.ndjson").read_text()
    assert "worker_error" in log
    assert "sensitive worker detail" not in log
    replay = await native_command(ENGINE, "--replay", str(run_dir))
    assert replay["ok"] and replay["states_verified"] == 31
    assert replay["network_calls"] == 0
