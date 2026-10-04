import asyncio
import json
from types import SimpleNamespace

import pytest

from arena_intelligence.budget import CostLedger
from arena_intelligence.protocol import AnalystDraft
from arena_intelligence.reasoner import ClaudeProvider, Reasoner, frequency_assessment


class StubProvider:
    def __init__(self, observation, delay=0):
        self.observation = observation
        self.calls = []
        self.delay = delay

    async def produce(self, stage, prompt, output, max_tokens, run_id, usage):
        self.calls.append((stage, max_tokens, json.loads(prompt)))
        await asyncio.sleep(self.delay)
        base = frequency_assessment(self.observation, [self.observation]).model_dump()
        if output is AnalystDraft:
            base["checks"] = [
                {
                    "kind": "report_timing",
                    "window_start_tick": 0,
                    "window_end_tick": self.observation.cutoff_tick,
                }
            ]
        return output.model_validate(base)


async def test_two_call_pipeline_has_equal_output_budget_and_local_checks(observation):
    provider = StubProvider(observation)
    result = await Reasoner("multi", provider).cycle(observation, [observation], {"e0"})
    assert result["status"] == "valid"
    assert [(c[0], c[1]) for c in provider.calls] == [("analyst", 4096), ("challenger", 4096)]
    assert provider.calls[1][2]["local_checks"][0]["mean_delay_ticks"] == 20
    assert result["assessment"]["cutoff_tick"] == 100
    single = StubProvider(observation)
    result = await Reasoner("single", single).cycle(observation, [observation], {"e0"})
    assert result["status"] == "valid"
    assert single.calls[0][1] == 8192


async def test_deadline_covers_both_calls_not_each_call(observation):
    provider = StubProvider(observation, delay=0.04)
    result = await Reasoner("multi", provider).cycle(observation, [observation], {"e0"}, 0.06)
    assert result["status"] == "timeout"
    assert len(provider.calls) == 2
    assert result["draft"] is not None
    assert result["assessment"] is None


async def test_future_history_is_never_sent_to_provider(observation):
    provider = StubProvider(observation)
    future = observation.model_copy(update={"cutoff_tick": 101})
    result = await Reasoner("multi", provider).cycle(observation, [future], {"e0"})
    assert result["status"] == "invalid"
    assert provider.calls == []


async def test_sdk_request_has_structured_format_and_settles_even_invalid_output(
    observation,
    tmp_path,
):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="{}")],
        )

    ledger = CostLedger(tmp_path / "cost.sqlite3")
    provider = ClaudeProvider(
        ledger, "dev", SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    result = await Reasoner("single", provider).cycle(observation, [observation], {"e0"})
    assert result["status"] == "invalid"
    assert captured["model"] == "claude-sonnet-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["thinking"] == {"type": "between_tools"}
    assert ledger.summary()["unreconciled_requests"] == 0
    assert result["cost_usd"] == pytest.approx(0.0007)


async def test_sdk_cancellation_keeps_reservation(observation, tmp_path):
    async def create(**kwargs):
        await asyncio.sleep(10)

    ledger = CostLedger(tmp_path / "cost.sqlite3")
    provider = ClaudeProvider(
        ledger, "dev", SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    result = await Reasoner("single", provider).cycle(observation, [observation], {"e0"}, 0.01)
    assert result["status"] == "timeout"
    assert ledger.summary()["unreconciled_requests"] == 1
    assert ledger.summary()["charged_or_reserved_usd"] > 0


def test_workspace_header_is_optional_and_loaded_only_from_env(tmp_path, monkeypatch):
    import anthropic

    captured = {}
    sentinel = object()

    def factory(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-key")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "workspace-test")
    ledger = CostLedger(tmp_path / "header.sqlite3")
    provider = ClaudeProvider(ledger, "dev")
    assert provider.client is sentinel
    assert captured["default_headers"] == {"anthropic-workspace-id": "workspace-test"}
    assert captured["max_retries"] == 0
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID")
    ClaudeProvider(ledger, "dev")
    assert captured["default_headers"] == {}
    assert ledger.summary()["requests"] == 0


async def test_mock_explores_and_cites_permitted_occupancy(observation):
    from arena_intelligence.protocol import Evidence, Friendly

    observation.observation.friendly = [
        Friendly(id=i, team=0, x=1, y=1, objective_id=0) for i in range(6)
    ]
    observation.observation.evidence.append(
        Evidence(
            id="node-report",
            tick=80,
            kind="node_status",
            data={
                "node_id": 2,
                "friendly_count": 2,
                "opponent_count": 5,
                "observed_tick": 80,
                "delivered_tick": 100,
            },
        )
    )
    result = await Reasoner("mock").cycle(observation, [observation], {"e0", "node-report"})
    final = result["assessment"]["assessment"]
    assert result["status"] == "valid"
    assert [a["objective_id"] for a in final["assignments"]] == [0, 1, 2, 0, 1, 2]
    assert final["hypotheses"][0]["evidence_ids"] == ["node-report"]
    assert "5 sensor-visible opposing bots" in final["hypotheses"][0]["claim"]
    assert final["forecast"]["probabilities"][2] > 0.25
    assert len(result["checks"]) == 3
    assert result["cost_usd"] == 0


async def test_definitive_api_rejection_releases_reservation_at_zero(observation, tmp_path):
    import anthropic

    async def create(**kwargs):
        raise anthropic.BadRequestError(
            "invalid request",
            response=SimpleNamespace(status_code=400, request=object(), headers={}),
            body={"error": {"type": "invalid_request_error"}},
        )

    ledger = CostLedger(tmp_path / "rejected.sqlite3")
    provider = ClaudeProvider(
        ledger, "dev", SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    result = await Reasoner("single", provider).cycle(observation, [observation], {"e0"})
    assert result["status"] == "invalid"
    assert result["usage"][0]["status"] == "rejected_not_billed"
    assert result["cost_usd"] == 0
    assert ledger.summary()["requests"] == 1
    assert ledger.summary()["unreconciled_requests"] == 0
    assert ledger.summary()["charged_or_reserved_usd"] == 0


def test_common_cycle_ceiling_blocks_excess_worst_case_calls():
    from arena_intelligence.budget import BudgetExceeded, worst_case_cost
    from arena_intelligence.reasoner import CycleSpend

    spend = CycleSpend()
    worst = worst_case_cost(100, 100, 4096)
    first = spend.reserve(worst)
    spend.reserve(worst)
    with pytest.raises(BudgetExceeded, match="per-cycle"):
        spend.reserve(worst)
    spend.settle(first, 1_000_000)
    spend.reserve(worst)
    assert sum(spend.amounts) <= spend.ceiling_nano


async def test_logs_expose_fixed_equal_live_allowances_and_single_tradeoff(observation):
    for backend in ["single", "multi"]:
        result = await Reasoner(backend, StubProvider(observation)).cycle(
            observation, [observation], {"e0"}
        )
        config = result["model_config"]
        assert config["model"] == "claude-sonnet-5-5"
        assert config["effort"] == "medium"
        assert config["thinking"] == "between_tools"
        assert config["total_output_token_allowance"] == 8192
        assert result["cycle_budget"]["ceiling_usd"] == 5
        if backend == "single":
            assert result["single_call_tradeoff"]
