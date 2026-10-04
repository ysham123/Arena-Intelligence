import json

import pytest
from test_python_reasoner import StubProvider

from arena_intelligence.context import (
    MAX_EVIDENCE_RECORDS,
    MAX_PROMPT_BYTES,
    compact_context,
    encode_prompt,
    initial_check_summaries,
    permitted_records,
)
from arena_intelligence.protocol import ObservationEnvelope
from arena_intelligence.reasoner import Reasoner


@pytest.fixture
def dense_history(observation):
    history = []
    for index in range(8):
        data = observation.model_dump()
        tick = (index + 1) * 300
        data["request_id"] = f"r{index}"
        data["cutoff_tick"] = tick
        obs = data["observation"]
        obs["tick"] = tick
        obs["scores"] = [index * 10, index * 8]
        obs["friendly"] = [
            {"id": i, "team": 0, "x": i % 32, "y": 1, "objective_id": i % 3} for i in range(128)
        ]
        obs["blocked"] = [[x, 10] for x in range(32)]
        evidence = []
        for j in range(128):
            source = tick - 150 + j
            if j % 3 == 0:
                kind = "node_status"
                fields = {
                    "node_id": j % 9 // 3,
                    "friendly_count": j % 6,
                    "opponent_count": j % 7,
                }
            else:
                kind = "sighting"
                fields = {"bot_id": j % 6 + 128, "x": 5, "y": 5}
            evidence.append(
                {
                    "id": f"e{index}-{j}",
                    "tick": source,
                    "kind": kind,
                    "data": {**fields, "observed_tick": source, "delivered_tick": source + 20},
                }
            )
        obs["evidence"] = evidence
        history.append(ObservationEnvelope.model_validate(data))
    return history


async def test_dense_payload_bounds_preserve_all_friendlies_and_shared_initial_facts(dense_history):
    request = dense_history[-1]
    allowed = {e.id for h in dense_history for e in h.observation.evidence}
    payloads = {}
    for backend in ("single", "multi"):
        provider = StubProvider(request)
        result = await Reasoner(backend, provider).cycle(request, dense_history, allowed)
        assert result["status"] == "valid"
        payloads[backend] = provider.calls[0][2]
        for stage, _, payload in provider.calls:
            prompt = encode_prompt(payload)
            assert len(prompt.encode()) <= MAX_PROMPT_BYTES
            assert result["prompt_bytes"][stage] == len(prompt.encode())
        context = payloads[backend]["context"]
        assert {b["id"] for b in context["current"]["friendly"]} == set(range(128))
        assert context["rules"]["tick_hz"] == 10
        assert context["rules"]["assignment_expiry_tick"] == request.cutoff_tick + 450
        assert context["rules"]["forecast_horizon_tick"] == request.cutoff_tick + 300
        assert "recommended assignments are accepted" in context["rules"]["forecast_condition"]
        assert [s["cutoff_tick"] for s in context["past_scores"]] == [1200, 1500, 1800, 2100]
        assert context["coverage"]["unique_received_evidence_count"] == 1024
        assert context["coverage"]["represented_evidence_count"] <= MAX_EVIDENCE_RECORDS
        assert context["coverage"]["omitted_received_evidence_count"] > 900
        checks = payloads[backend]["initial_checks"]
        assert {c["kind"] for c in checks} == {
            "objective_visits",
            "occupancy_trends",
            "report_timing",
        }
        assert checks[0]["observed_report_counts"]["0"] == 8 * 85
        assert checks[2]["mean_delay_ticks"] == 20
        raw_old_prompt = json.dumps(
            {
                "current": request.model_dump(),
                "history": [h.model_dump() for h in dense_history[-4:]],
            }
        )
        assert len(encode_prompt(payloads[backend]).encode()) < len(raw_old_prompt.encode()) / 4
    assert payloads["single"]["context"] == payloads["multi"]["context"]
    assert payloads["single"]["initial_checks"] == payloads["multi"]["initial_checks"]


async def test_follow_up_checks_use_full_history_after_compaction(dense_history):
    request = dense_history[-1]
    allowed = {e.id for h in dense_history for e in h.observation.evidence}
    provider = StubProvider(request)
    result = await Reasoner("multi", provider).cycle(request, dense_history, allowed)
    # The model sees bounded citations; local computation still sees all 1,024 records.
    assert len(result["checks"][0]["evidence_ids"]) == 1024
    assert result["checks"][0]["report_count"] == 8 * 85
    challenger = provider.calls[1][2]
    assert len(challenger["local_checks"][0]["evidence_ids"]) <= 8
    assert challenger["local_checks"][0]["report_count"] == 8 * 85


def test_compact_context_keeps_source_ids_times_and_whitelists_public_fields(observation):
    from arena_intelligence.protocol import ReportedOpponent

    observation.observation.last_seen = [
        ReportedOpponent(id=8, team=1, x=3, y=4, observed_tick=10, evidence_id="old-source")
    ]
    # Defensive whitelist still excludes accidentally attached private attributes.
    observation.observation.__dict__["rng_state"] = "private-rng"
    observation.observation.evidence[0].data["hidden_position"] = 999
    history = [observation]
    records = permitted_records(history, 100)
    context = compact_context(observation, history, records)
    summaries = initial_check_summaries(history, 100, records)
    payload = encode_prompt({"context": context, "initial_checks": summaries})
    for forbidden in ("rng_state", "private-rng", "hidden_position", "opponent_policy", "seed"):
        assert forbidden not in payload
    assert "permitted_history" not in payload
    assert "schema_version" not in payload
    latest = {r["evidence_id"]: r for r in context["latest_received_sightings"]}
    assert latest["e0"]["observed_tick"] == 70
    assert latest["e0"]["delivered_tick"] == 90
    assert latest["old-source"]["observed_tick"] == 10
    assert latest["old-source"]["delivered_tick"] is None
    assert context["past_scores"] == []  # Current is not repeated as a history snapshot.
    assert "zero does not prove empty" in context["coverage"]["limitation"]


def test_prompt_limit_rejects_oversize_without_truncating_essential_state():
    with pytest.raises(ValueError, match="32000-byte"):
        encode_prompt({"essential_state": "x" * MAX_PROMPT_BYTES})
