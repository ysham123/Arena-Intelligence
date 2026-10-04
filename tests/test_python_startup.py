import asyncio
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from arena_intelligence.protocol import RunConfig
from arena_intelligence.reasoner import Reasoner
from arena_intelligence.runner import NativeProtocolError, run_match

ENGINE = Path(
    os.environ.get("ARENA_ENGINE", Path(__file__).resolve().parents[1] / "build/release/arena-sim")
)


@pytest.mark.parametrize(
    "values",
    [
        {"seed": -1},
        {"seed": 2**64},
        {"duration_ticks": 36001},
        {"tick_hz": 9},
        {"tick_hz": 10.0},
        {"reasoner_team": True},
        {"planning_ticks": list(range(129))},
        {"bots_per_team": 129},
        {"report_delay_ticks": 301},
    ],
)
def test_native_configuration_rejects_unsupported_values_before_launch(values):
    with pytest.raises(ValidationError):
        RunConfig(**values)


def test_native_configuration_accepts_uint64_and_native_maxima():
    config = RunConfig(seed=2**64 - 1, duration_ticks=36000, planning_ticks=list(range(128)))
    assert config.seed == 2**64 - 1
    assert config.tick_hz == 10
    assert len(config.planning_ticks) == 128


async def test_native_start_error_fails_promptly_and_child_closes_cleanly(tmp_path):
    script = tmp_path / "rejecting-native"
    marker = tmp_path / "clean-eof"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "for line in sys.stdin:\n"
        "    command=json.loads(line)\n"
        "    if command['type']=='start':\n"
        "        print(json.dumps(dict(type='error',schema_version=1,run_id=command['run_id'],"
        "reason='start_failed: sensitive rejection detail')),flush=True)\n"
        f"Path({str(marker)!r}).write_text('closed')\n"
    )
    script.chmod(0o755)
    with pytest.raises(NativeProtocolError) as captured:
        await asyncio.wait_for(
            run_match(script, tmp_path / "recording", RunConfig(), Reasoner("mock")), timeout=5
        )
    assert "sensitive rejection detail" not in str(captured.value)
    assert marker.read_text() == "closed"


@pytest.mark.skipif(not ENGINE.exists(), reason="build the native release engine first")
async def test_actual_native_record_open_failure_is_reported_without_waiting_for_match(tmp_path):
    directory = tmp_path / "unusable-recording"
    (directory / "commands.ndjson").mkdir(parents=True)
    with pytest.raises(NativeProtocolError):
        await asyncio.wait_for(
            run_match(
                ENGINE,
                directory,
                RunConfig(duration_ticks=10, planning_ticks=[0]),
                Reasoner("mock"),
            ),
            timeout=5,
        )
    assert "start_failed" in (directory / "host_events.ndjson").read_text()
