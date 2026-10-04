"""Matched-input evaluation tests use scripted providers and zero network calls."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from arena_intelligence.protocol import AnalystDraft, Assessment, validate_assessment
from arena_intelligence.reasoner import MODEL, Reasoner

spec = importlib.util.spec_from_file_location(
    "matched_benchmark", Path(__file__).resolve().parents[1] / "tools" / "compare_observations.py"
)
assert spec is not None and spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
spec.loader.exec_module(benchmark)
CUTOFFS = benchmark.CUTOFFS
check_model_access = benchmark.check_model_access
evaluate_matched = benchmark.evaluate_matched
load_reference = benchmark.load_reference
main = benchmark.main
options = benchmark.options
run_reference = benchmark.run_reference


def make_reference(tmp_path, observation):
    directory = tmp_path / "scenario-000-mock"
    directory.mkdir()
    records = []
    for index, cutoff in enumerate(CUTOFFS):
        item = observation.model_dump()
        item.update(run_id="reference-run", request_id=f"r{index}", cutoff_tick=cutoff)
        item["observation"]["tick"] = cutoff
        if cutoff == 0:
            item["observation"]["evidence"] = []
        records.append(item)
    (directory / "observations.ndjson").write_text(
        "".join(json.dumps(item) + "\n" for item in records)
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "reference-run",
                "engine_version": "0.1.0",
                "schema_version": 1,
                "config": {"seed": 123, "opponent_policy": "nearest", "mirrored": False},
            }
        )
    )
    (directory / "results.json").write_text(json.dumps({"status": "completed", "tick": 1800}))
    (directory / "states.ndjson").write_text(
        "".join(
            json.dumps(
                {
                    "state": {
                        "tick": cutoff + 300,
                        "nodes": records[0]["observation"]["nodes"],
                        "bots": [{"id": 1, "team": 1, "x": 5, "y": 5}],
                    }
                }
            )
            + "\n"
            for cutoff in CUTOFFS
        )
    )
    return directory


def assessment(request):
    return Assessment.model_validate(
        {
            "hypotheses": [
                {
                    "claim": "Prior: objective occupancy remains uncertain",
                    "evidence_ids": [],
                    "alternative": "An alternative objective may dominate",
                }
            ],
            "forecast": {
                "horizon_tick": request.cutoff_tick + 300,
                "probabilities": [1.0, 0.0, 0.0, 0.0],
            },
            "assignments": [
                {"bot_id": b.id, "objective_id": 0} for b in request.observation.friendly
            ],
        }
    )


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, amount):
        self.waits.append(amount)
        self.now += amount
        await asyncio.sleep(0)


class RecordingReasoner:
    def __init__(self, failures=None, mutate=False, latency=0.01):
        self.calls = []
        self.failures = failures or {}
        self.mutate = mutate
        self.latency = latency
        self.inflight = 0
        self.maximum_inflight = 0

    async def cycle(self, request, history, known_evidence, deadline_seconds):
        self.inflight += 1
        self.maximum_inflight = max(self.maximum_inflight, self.inflight)
        self.calls.append(
            (
                request.model_dump(),
                [h.model_dump() for h in history],
                set(known_evidence),
                deadline_seconds,
            )
        )
        if self.mutate:
            history[0].observation.friendly[0].x = 99
        await asyncio.sleep(0)
        self.inflight -= 1
        status = self.failures.get(request.cutoff_tick, "valid")
        return {
            "status": status,
            "latency_seconds": self.latency,
            "assessment": validate_assessment(
                assessment(request), request, known_evidence
            ).model_dump()
            if status == "valid"
            else None,
            "validation_category": "evidence" if status == "invalid" else None,
            "error": "WorkerError" if status == "worker_error" else None,
            "usage": [
                {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.1,
                    "status": "uncertain" if status == "timeout" else "settled",
                }
            ],
            "cost_usd": 0.1,
        }


async def test_identical_deep_copied_histories_pacing_and_no_privileged_reads(
    tmp_path, observation, monkeypatch
):
    path = make_reference(tmp_path, observation)
    privileged_allowed = False
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.name in {"manifest.json", "results.json", "states.ndjson"}:
            assert privileged_allowed, "truth was opened before all model calls finished"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    reference = load_reference(path)
    first, second, frequency = (
        RecordingReasoner(mutate=True),
        RecordingReasoner(),
        RecordingReasoner(),
    )
    clock = VirtualClock()
    root = tmp_path / "matched"
    await run_reference(
        reference,
        root / path.name,
        {"single": first, "multi": second, "frequency": frequency},
        sleep=clock.sleep,
        clock=clock,
    )
    assert first.calls == second.calls == frequency.calls
    assert [call[0]["cutoff_tick"] for call in first.calls] == list(CUTOFFS)
    assert all(call[3] == 20 for call in first.calls)
    assert clock.waits == [0, 30, 30, 30, 30, 30]
    assert first.maximum_inflight == second.maximum_inflight == frequency.maximum_inflight == 1
    assert reference.observations[0].observation.friendly[0].x == 1
    privileged_allowed = True
    result = evaluate_matched([reference], root, ["single", "multi", "frequency"])
    for backend, group in result["configurations"].items():
        assert group["planning_opportunities"] == group["scored_forecasts"] == 6
        assert group["forecast_accuracy"] == 1
        assert group["brier_score"] == 0
        if backend != "frequency":
            assert group["model_config"]["model"] == MODEL
            assert group["model_config"]["total_output_token_allowance"] == 8192
        assert "wins" not in group and "score_mean" not in group
    assert all(row["native_acceptance"] == "not evaluated" for row in result["forecasts"])


class StubProvider:
    def __init__(self):
        self.request = None
        self.calls = []

    async def produce(self, stage, prompt, output, max_tokens, run_id, usage):
        self.calls.append((stage, prompt, max_tokens))
        result = assessment(self.request).model_dump()
        if output is AnalystDraft:
            result["checks"] = []
        return output.model_validate(result)


class BoundReasoner(Reasoner):
    async def cycle(self, request, history, known_evidence, deadline_seconds=20):
        self.provider.request = request
        return await super().cycle(request, history, known_evidence, deadline_seconds)


async def test_actual_reasoner_configurations_receive_identical_analyst_input(
    tmp_path, observation
):
    path = make_reference(tmp_path, observation)
    reference = load_reference(path)
    single, multi = StubProvider(), StubProvider()
    await run_reference(
        reference,
        tmp_path / "matched" / path.name,
        {
            "single": BoundReasoner("single", single),
            "multi": BoundReasoner("multi", multi),
        },
        paced=False,
    )
    single_analyst = [item for item in single.calls if item[0] == "analyst"]
    multi_analyst = [item for item in multi.calls if item[0] == "analyst"]
    assert [item[1] for item in single_analyst] == [item[1] for item in multi_analyst]
    assert {item[2] for item in single_analyst} == {8192}
    assert {item[2] for item in multi.calls} == {4096}
    assert len(single.calls) == 6 and len(multi.calls) == 12


async def test_failed_missing_and_late_cycles_keep_full_opportunity_denominator(
    tmp_path, observation
):
    path = make_reference(tmp_path, observation)
    reference = load_reference(path)
    root = tmp_path / "matched"
    failed = RecordingReasoner(
        {300: "timeout", 600: "invalid", 900: "worker_error", 1200: "cancelled"}
    )
    late = RecordingReasoner(latency=20.001)
    await run_reference(reference, root / path.name, {"single": failed, "multi": late}, paced=False)
    cycle_path = root / path.name / "single" / "matched_cycles.ndjson"
    lines = [json.loads(line) for line in cycle_path.read_text().splitlines()]
    # A process interruption can leave the last start event without its completed cycle.
    cycle_path.write_text(
        "".join(
            json.dumps(line) + "\n"
            for line in lines
            if not (line["type"] == "matched_cycle" and line["cutoff_tick"] == 1500)
        )
    )
    result = evaluate_matched([reference], root, ["single", "multi"])
    single = result["configurations"]["single"]
    assert single["planning_opportunities"] == 6
    assert single["scored_forecasts"] == 1
    assert single["forecast_coverage"] == pytest.approx(1 / 6)
    assert single["status_counts"]["missing"] == 1
    assert single["evidence_errors"] == 1
    assert single["known_cost_usd"] == pytest.approx(0.5)
    assert single["cost_complete"] is False
    multi = result["configurations"]["multi"]
    assert multi["status_counts"] == {"late": 6}
    assert multi["forecast_coverage"] == 0
    assert multi["forecast_accuracy"] is None


async def test_history_fingerprint_mismatch_is_rejected(tmp_path, observation):
    path = make_reference(tmp_path, observation)
    reference = load_reference(path)
    root = tmp_path / "matched"
    await run_reference(reference, root / path.name, {"single": RecordingReasoner()}, paced=False)
    cycle_path = root / path.name / "single" / "matched_cycles.ndjson"
    records = [json.loads(line) for line in cycle_path.read_text().splitlines()]
    records[1]["input_history_sha256"] = "different-history"
    cycle_path.write_text("".join(json.dumps(row) + "\n" for row in records))
    with pytest.raises(ValueError, match="different public history"):
        evaluate_matched([reference], root, ["single"])


async def test_model_access_preflight_uses_model_endpoint_without_paid_inference():
    calls = []

    async def retrieve(model):
        calls.append(model)
        return SimpleNamespace(id=model)

    provider = SimpleNamespace(client=SimpleNamespace(models=SimpleNamespace(retrieve=retrieve)))
    assert (await check_model_access(provider))["accessible_model_id"] == MODEL
    assert calls == [MODEL]


async def test_paid_guard_precedes_provider_creation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("paid provider created without authorization")

    monkeypatch.setattr(benchmark, "ClaudeProvider", forbidden)
    with pytest.raises(ValueError, match="allow-paid"):
        await main(options([]))


async def test_offline_cli_exercises_saved_report_without_api(tmp_path, observation, monkeypatch):
    path = make_reference(tmp_path, observation)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline benchmark attempted SDK construction")

    monkeypatch.setattr(benchmark, "ClaudeProvider", forbidden)
    output = tmp_path / "report.json"
    args = options(
        [
            "--offline",
            "--references",
            str(path),
            "--run-root",
            str(tmp_path / "matched"),
            "--output",
            str(output),
            "--jobs",
            "1",
        ]
    )
    await main(args)
    report = json.loads(output.read_text())
    assert report["metadata"]["measurement_kind"] == "offline"
    assert report["metadata"]["paced"] is False
    assert report["metadata"]["maximum_concurrent_paid_cycles"] == 0
    assert len(report["metadata"]["source_sha256"]) == 7
    assert set(report["configurations"]) == {"mock", "frequency"}
    assert output.with_suffix(".md").is_file()
    assert all(group["planning_opportunities"] == 6 for group in report["configurations"].values())


async def test_paid_defaults_include_frequency_with_separate_concurrency_metadata(
    tmp_path, observation, monkeypatch
):
    path = make_reference(tmp_path, observation)
    closed = []

    async def retrieve(model):
        return SimpleNamespace(id=model)

    class LocalProvider:
        def __init__(self, ledger, bucket):
            assert bucket == "eval"
            self.client = SimpleNamespace(models=SimpleNamespace(retrieve=retrieve))

        async def close(self):
            closed.append(True)

    real_run = run_reference

    async def unpaced_local_run(reference, directory, reasoners, **kwargs):
        assert set(reasoners) == {"frequency", "single", "multi"}
        await real_run(reference, directory, reasoners, paced=False)

    monkeypatch.setattr(benchmark, "ClaudeProvider", LocalProvider)
    monkeypatch.setattr(benchmark, "Reasoner", lambda backend, provider: RecordingReasoner())
    monkeypatch.setattr(benchmark, "run_reference", unpaced_local_run)
    output = tmp_path / "paid-config-report.json"
    await main(
        options(
            [
                "--allow-paid",
                "--references",
                str(path),
                "--run-root",
                str(tmp_path / "matched"),
                "--output",
                str(output),
                "--ledger",
                str(tmp_path / "costs.sqlite3"),
                "--jobs",
                "4",
            ]
        )
    )
    report = json.loads(output.read_text())
    assert set(report["configurations"]) == {"frequency", "single", "multi"}
    assert report["metadata"]["maximum_concurrent_configuration_cycles"] == 12
    assert report["metadata"]["maximum_concurrent_paid_cycles"] == 8
    assert closed == [True]
