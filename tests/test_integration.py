"""Cross-process native contract checks; no API credentials or calls."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path(os.environ.get("ARENA_ENGINE", ROOT / "build/release/arena-sim"))
pytestmark = pytest.mark.skipif(not ENGINE.is_file(), reason="Build arena-sim first")


class Native:
    def __init__(self, record_dir: Path, *, paced=False, duration=600):
        self.proc = subprocess.Popen(
            [str(ENGINE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.messages = queue.Queue()
        self.run_id = record_dir.name
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.send(
            {
                "type": "start",
                "config": {
                    "seed": 91,
                    "duration_ticks": duration,
                    "planning_ticks": [0, 300] if duration >= 300 else [0],
                    "paced": paced,
                    "report_drop_rate": 0.2,
                },
                "record_dir": str(record_dir),
            }
        )
        self.until("ready")
        self.initial = self.until("observation")

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                self.messages.put({"type": "bad_stdout"})
        self.messages.put({"type": "eof"})

    def send(self, message):
        message = {"schema_version": 1, "run_id": self.run_id, **message}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def until(self, kind, timeout=10):
        while True:
            message = self.messages.get(timeout=timeout)
            assert message["type"] not in {"bad_stdout", "eof"}, message
            if message["type"] == kind:
                return message

    def assessment(self, obs=None):
        obs = obs or self.initial
        cutoff = obs["cutoff_tick"]
        return {
            "type": "assessment",
            "request_id": obs["request_id"],
            "cutoff_tick": cutoff,
            "expiry_tick": cutoff + 450,
            "assessment": {
                "hypotheses": [
                    {
                        "claim": "Prior: insufficient observed evidence",
                        "evidence_ids": [],
                        "alternative": "Another policy is possible",
                    }
                ],
                "forecast": {"horizon_tick": cutoff + 300, "probabilities": [0.25] * 4},
                "assignments": [
                    {"bot_id": b["id"], "objective_id": i % 3}
                    for i, b in enumerate(obs["observation"]["friendly"])
                ],
            },
        }

    def close(self):
        if self.proc.poll() is None:
            try:
                self.send({"type": "stop"})
                self.proc.wait(timeout=5)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.thread.join(timeout=1)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe:
                pipe.close()


def test_atomic_assignments_and_exact_replay(tmp_path):
    run_dir = tmp_path / "replay-test"
    n = Native(run_dir)
    try:
        n.send(n.assessment())
        result = n.until("assessment_result")
        assert result["accepted"] is True
        assert result["application_tick"] == 1
        n.send({"type": "advance", "ticks": 300})
        obs = n.until("observation")
        assert obs["cutoff_tick"] == 300
        assert [b["objective_id"] for b in obs["observation"]["friendly"]] == [
            i % 3 for i in range(6)
        ]
        n.until("advanced")
        n.send(n.assessment(obs))
        assert n.until("assessment_result")["accepted"] is True
        n.send({"type": "advance", "ticks": 300})
        n.until("finished")
        assert n.proc.wait(timeout=5) == 0
    finally:
        n.close()
    replay = subprocess.run(
        [str(ENGINE), "--replay", str(run_dir)], capture_output=True, text=True, timeout=15
    )
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert json.loads(replay.stdout)


def test_unknown_evidence_and_previous_run_are_rejected(tmp_path):
    n = Native(tmp_path / "reject-test")
    try:
        bad = n.assessment()
        bad["assessment"]["hypotheses"][0]["evidence_ids"] = ["private-truth"]
        n.send(bad)
        r = n.until("assessment_result")
        assert r["accepted"] is False
        assert "evidence" in r["reason"].lower()
        foreign = n.assessment()
        foreign["run_id"] = "different-run"
        n.send(foreign)
        assert n.until("assessment_result")["accepted"] is False
    finally:
        n.close()


def test_incomplete_assignment_does_not_mutate_world(tmp_path):
    run_dir = tmp_path / "incomplete-test"
    n = Native(run_dir)
    initial = [b["objective_id"] for b in n.initial["observation"]["friendly"]]
    try:
        bad = n.assessment()
        bad["assessment"]["assignments"].pop()
        n.send(bad)
        assert n.until("assessment_result")["accepted"] is False
        n.send({"type": "advance", "ticks": 1})
        n.until("advanced")
    finally:
        n.close()
    states = [json.loads(s) for s in (run_dir / "states.ndjson").read_text().splitlines()]
    state = states[-1].get("state", states[-1])
    assert [b["objective_id"] for b in state["bots"] if b["team"] == 0] == initial


def test_observation_has_no_hidden_policy_or_truth_fields(tmp_path):
    n = Native(tmp_path / "isolation-test")
    try:
        text = json.dumps(n.initial["observation"])
        for forbidden in ["opponent_policy", "rng_state", "truth_path", "hidden_state"]:
            assert forbidden not in text
        assert n.initial["observation"]["visible_opponents"] == []
    finally:
        n.close()


def test_paced_engine_survives_host_reasoning_delay(tmp_path):
    n = Native(tmp_path / "delay-test", paced=True, duration=15)
    try:
        finished = n.until("finished", timeout=5)
        assert finished.get("tick", 15) >= 15
        assert n.proc.wait(timeout=5) == 0
    finally:
        n.close()
