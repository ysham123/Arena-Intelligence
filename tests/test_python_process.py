import asyncio
import json
import sys
from pathlib import Path

import pytest

from arena_intelligence.process import NativeProcess
from arena_intelligence.protocol import RunConfig
from arena_intelligence.reasoner import Reasoner
from arena_intelligence.runner import run_match


@pytest.fixture
def fake_engine(tmp_path):
    script = tmp_path / "fake-native"
    script.write_text("""#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin:
    command=json.loads(line)
    if command['type']=='start':
        run=command['run_id']; config=command['config']; tick=0
        def emit(kind, **fields):
            print(json.dumps(dict(type=kind,schema_version=1,run_id=run,**fields)),flush=True)
        def observe():
            emit('observation',request_id='r'+str(tick),cutoff_tick=tick,observation=dict(
                tick=tick,team=0,size=32,scores=[0,0],
                nodes=[dict(id=i,x=5+10*i,y=5+10*i) for i in range(3)],blocked=[],
                friendly=[dict(id=0,team=0,x=1,y=1,objective_id=0)],
                visible_opponents=[],last_seen=[],evidence=[]))
        sys.stderr.write('x'*300000);sys.stderr.flush()
        assert 'ANTHROPIC_API_KEY' not in os.environ
        emit('ready',tick=0);observe()
    elif command['type']=='assessment':
        emit('assessment_result',request_id=command['request_id'],accepted=True,
             application_tick=tick+1,reason='accepted',remaining_horizon_ticks=300)
    elif command['type']=='fallback':
        emit('fallback_result',request_id=command['request_id'],accepted=True,
             application_tick=tick+1,reason='accepted')
    elif command['type']=='advance':
        tick+=command['ticks']
        if tick>=config['duration_ticks']:
            emit('finished',tick=tick,scores=[0,0],winner=-1);break
        observe();emit('advanced',tick=tick)
    elif command['type']=='stop':
        emit('stopped',tick=tick,scores=[0,0]);break
""")
    script.write_text(script.read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    script.chmod(0o755)
    return script


async def test_batch_host_drains_stderr_and_applies_before_advancing(
    fake_engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-secret")
    run_dir = tmp_path / "run"
    config = RunConfig(duration_ticks=600, planning_ticks=[0, 300])
    summary = await asyncio.wait_for(run_match(fake_engine, run_dir, config, Reasoner("mock")), 15)
    assert summary["terminal"]["type"] == "finished"
    assert summary["planning_opportunities"] == 2
    assert summary["completed_cycles"] == 2
    assert (run_dir / "native.stderr.log").stat().st_size == 300000
    events = [
        json.loads(line) for line in (run_dir / "host_events.ndjson").read_text().splitlines()
    ]
    assert [e["application_tick"] for e in events if e["type"] == "assessment_result"] == [1, 301]
    logs = [json.loads(line) for line in (run_dir / "reasoning.ndjson").read_text().splitlines()]
    assert all(log["status"] == "valid" for log in logs)


async def test_batch_rejects_live_backend_before_launch(tmp_path):
    class Dummy:
        pass

    with pytest.raises(ValueError, match="batch"):
        await run_match(
            Path("does-not-exist"), tmp_path / "run", RunConfig(), Reasoner("single", Dummy())
        )
    assert not (tmp_path / "run").exists()


async def test_malformed_native_stdout_is_reported_and_process_reaped(tmp_path):
    script = tmp_path / "bad-native"
    script.write_text("#!/usr/bin/env python3\nprint('not-json',flush=True)\n")
    script.write_text(script.read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    script.chmod(0o755)
    async with NativeProcess(script) as native:
        with pytest.raises(json.JSONDecodeError):
            await native.receive(10)
    assert native.process.returncode == 0
    assert all(task.done() for task in native.tasks)


async def test_mock_timeout_records_missing_assessment_then_batch_continues(fake_engine, tmp_path):
    run_dir = tmp_path / "delayed"
    summary = await run_match(
        fake_engine,
        run_dir,
        RunConfig(duration_ticks=600, planning_ticks=[0, 300]),
        Reasoner("mock", mock_delay_seconds=0.05),
        deadline_seconds=0.01,
    )
    assert summary["terminal"]["type"] == "finished"
    logs = [json.loads(line) for line in (run_dir / "reasoning.ndjson").read_text().splitlines()]
    assert [log["status"] for log in logs] == ["timeout", "timeout"]
    assert all(log["assessment"] is None for log in logs)
