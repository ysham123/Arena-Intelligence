"""Native IPC/replay acceptance tests using only the Python standard library."""
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ENGINE = Path(sys.argv.pop(1)).resolve()
TIMEOUT_SCALE = float(os.environ.get("ARENA_TEST_TIMEOUT_SCALE", "1"))
if not math.isfinite(TIMEOUT_SCALE) or TIMEOUT_SCALE <= 0:
    raise ValueError("ARENA_TEST_TIMEOUT_SCALE must be finite and positive")


class Process:
    def __init__(self, directory, *, paced=False, duration=600):
        self.run_id = "transport-test"
        self.proc = subprocess.Popen([str(ENGINE)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.output = queue.Queue()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.send({"type": "start", "config": {"paced": paced,
            "duration_ticks": duration, "planning_ticks": [0, 300] if duration >= 300 else [0]},
            "record_dir": str(directory)})
        self.until("ready")
        self.initial = self.until("observation")

    def read(self):
        for line in self.proc.stdout:
            self.output.put(json.loads(line))
        self.output.put({"type": "eof"})

    def send(self, value):
        envelope = {"schema_version": 1, "run_id": self.run_id, **value}
        self.proc.stdin.write(json.dumps(envelope) + "\n")
        self.proc.stdin.flush()

    def until(self, kind, timeout=5):
        while True:
            out = self.output.get(timeout=timeout * TIMEOUT_SCALE)
            if out["type"] == "eof":
                raise AssertionError("Premature EOF: " + self.proc.stderr.read())
            if out["type"] == kind:
                return out

    def close(self):
        if self.proc.poll() is None:
            try:
                self.send({"type": "stop"})
                self.proc.wait(timeout=5 * TIMEOUT_SCALE)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(timeout=5 * TIMEOUT_SCALE)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if not stream.closed:
                stream.close()
        self.reader.join(timeout=1 * TIMEOUT_SCALE)

    def assessment(self):
        return {"type": "assessment", "request_id": self.initial["request_id"],
            "cutoff_tick": 0, "expiry_tick": 450, "assessment": {
                "hypotheses": [{"claim": "Prior: insufficient evidence",
                    "evidence_ids": [], "alternative": "A different policy remains plausible"}],
                "forecast": {"horizon_tick": 300, "probabilities": [.25] * 4},
                "assignments": [{"bot_id": b["id"], "objective_id": i % 3}
                    for i, b in enumerate(self.initial["observation"]["friendly"])]}}


class TransportTests(unittest.TestCase):
    def test_exact_replay_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            p = Process(run)
            try:
                p.send(p.assessment())
                result = p.until("assessment_result")
                self.assertTrue(result["accepted"])
                self.assertEqual(result["application_tick"], 1)
                self.assertEqual(result["remaining_horizon_ticks"], 299)
                p.send({"type": "advance", "ticks": 300})
                obs = p.until("observation")
                p.until("advanced")
                p.send({"type": "fallback", "request_id": obs["request_id"],
                    "cutoff_tick": 300, "reason": "worker_timeout"})
                fallback = p.until("fallback_result")
                self.assertTrue(fallback["accepted"])
                self.assertEqual(fallback["application_tick"], 301)
                p.send({"type": "advance", "ticks": 300})
                self.assertEqual(p.until("finished")["tick"], 600)
                self.assertEqual(p.proc.wait(timeout=5 * TIMEOUT_SCALE), 0)
            finally:
                p.close()
            replay = subprocess.run([str(ENGINE), "--replay", str(run)],
                capture_output=True, text=True, timeout=30 * TIMEOUT_SCALE)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertEqual(json.loads(replay.stdout)["states_verified"], 601)
            self.assertEqual(json.loads(replay.stdout)["network_calls"], 0)
            self.assertEqual(json.loads(replay.stdout)["commands_verified"], 2)
            lines = (run / "states.ndjson").read_text().splitlines()
            (run / "states.ndjson").write_text("\n".join(lines[:-10]) + "\n")
            truncated = subprocess.run([str(ENGINE), "--replay", str(run)],
                capture_output=True, text=True, timeout=30 * TIMEOUT_SCALE)
            self.assertNotEqual(truncated.returncode, 0)
            self.assertEqual(json.loads(truncated.stdout)["mismatch"], "final_result")
            altered = json.loads(lines[10])
            altered["checksum"] = "0000000000000000"
            lines[10] = json.dumps(altered)
            (run / "states.ndjson").write_text("\n".join(lines) + "\n")
            replay = subprocess.run([str(ENGINE), "--replay", str(run)],
                capture_output=True, text=True, timeout=30 * TIMEOUT_SCALE)
            self.assertNotEqual(replay.returncode, 0)
            self.assertEqual(json.loads(replay.stdout)["mismatch"], "state")

    def test_malformed_packets_do_not_stop_paced_simulation(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Process(Path(temp) / "run", paced=True, duration=15)
            try:
                p.send({"type": "advance", "schema_version": "wrong", "ticks": "wrong"})
                self.assertEqual(p.until("error")["reason"], "invalid_envelope")
                p.proc.stdin.write("not json\n" + "x" * 70000 + "\n")
                p.proc.stdin.flush()
                p.until("error")
                p.until("error")
                self.assertEqual(p.until("finished")["tick"], 15)
                self.assertEqual(p.proc.wait(timeout=5 * TIMEOUT_SCALE), 0)
            finally:
                p.close()

    def test_input_queue_pressure_preserves_processed_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            p = Process(run, duration=15)
            try:
                packet = {"type": "assessment", "schema_version": 1,
                    "run_id": p.run_id, "request_id": "unknown-pressure-request"}
                p.proc.stdin.write((json.dumps(packet) + "\n") * 300)
                p.proc.stdin.flush()
                for _ in range(300):
                    self.assertFalse(p.until("assessment_result")["accepted"])
                p.send({"type": "advance", "ticks": 15})
                p.until("finished")
                self.assertEqual(p.proc.wait(timeout=5 * TIMEOUT_SCALE), 0)
                self.assertEqual(len((run / "commands.ndjson").read_text().splitlines()), 300)
            finally:
                p.close()

    def test_blocked_stdout_exits_without_join_hang(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            proc = subprocess.Popen([str(ENGINE)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            errors = []
            def write():
                start = {"type": "start", "schema_version": 1, "run_id": "blocked",
                    "record_dir": str(run), "config": {"duration_ticks": 15, "planning_ticks": [0]}}
                packet = {"type": "assessment", "schema_version": 1,
                    "run_id": "blocked", "request_id": "unknown"}
                try:
                    proc.stdin.write(json.dumps(start) + "\n" + (json.dumps(packet) + "\n") * 2000)
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
                except Exception as exc:
                    errors.append(exc)
            writer = threading.Thread(target=write, daemon=True)
            writer.start()
            try:
                self.assertEqual(proc.wait(timeout=6 * TIMEOUT_SCALE), 2)
                writer.join(timeout=1 * TIMEOUT_SCALE)
                self.assertFalse(writer.is_alive())
                self.assertFalse(errors)
                self.assertEqual(json.loads((run / "results.json").read_text())["status"], "transport_failed")
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait(timeout=3 * TIMEOUT_SCALE)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        stream.close()
                    except BrokenPipeError:
                        pass

    def test_eof_stops_cleanly_and_flushes_records(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            p = Process(run, paced=True)
            try:
                p.proc.stdin.close()
                self.assertEqual(p.until("stopped")["type"], "stopped")
                self.assertEqual(p.proc.wait(timeout=5 * TIMEOUT_SCALE), 0)
                self.assertEqual(json.loads((run / "results.json").read_text())["status"], "eof")
                self.assertTrue((run / "states.ndjson").read_text().strip())
            finally:
                p.close()


if __name__ == "__main__":
    unittest.main()
