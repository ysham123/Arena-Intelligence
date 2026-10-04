import json
from pathlib import Path

import pytest

from arena_intelligence.reporting import summarize_run, summarize_tournament, write_report
from arena_intelligence.viewer import _public_telemetry, generate_viewer


def write_ndjson(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def recorded_run(tmp_path: Path, planning=(0, 300, 600), name="run") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    config = {
        "seed": 42,
        "reasoner_team": 0,
        "tick_hz": 10,
        "duration_ticks": 900,
        "planning_ticks": list(planning),
        "opponent_policy": "holder",
        "mirrored": False,
    }
    (directory / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "run_id": name, "config": config})
    )
    nodes = [{"id": 0, "x": 5, "y": 5}, {"id": 1, "x": 16, "y": 16}, {"id": 2, "x": 26, "y": 26}]
    states = []
    for tick in sorted(
        set(
            (0, 300, 400, 600, 900)
            + tuple(planning)
            + tuple(tick + 300 for tick in planning if tick + 300 <= 900)
        )
    ):
        # First two nodes tie positively; node 0 is the required truth label.
        bots = [
            {"id": 0, "team": 0, "x": 1, "y": 1, "objective_id": 0},
            {"id": 1, "team": 1, "x": 5, "y": 5, "objective_id": 0},
            {"id": 2, "team": 1, "x": 16, "y": 16, "objective_id": 1},
        ]
        states.append(
            {
                "tick": tick,
                "checksum": str(tick),
                "state": {
                    "tick": tick,
                    "size": 32,
                    "bots": bots,
                    "nodes": nodes,
                    "blocked": [[8, 8]],
                    "scores": [20, 10],
                    "secret_policy": "must not enter browser payload",
                },
            }
        )
    write_ndjson(directory / "states.ndjson", states)
    observations = [
        {
            "type": "observation",
            "schema_version": 1,
            "run_id": name,
            "request_id": f"r{tick}",
            "cutoff_tick": tick,
            "observation": {
                "tick": tick,
                "team": 0,
                "size": 32,
                "nodes": nodes,
                "blocked": [[8, 8]],
                "scores": [0, 0],
                "friendly": [states[0]["state"]["bots"][0]],
                "visible_opponents": [],
                "last_seen": [],
                "evidence": [
                    {
                        "id": "e1",
                        "tick": 0,
                        "kind": "node_status",
                        "data": {"node_id": 0, "opponent_count": 1, "friendly_count": 0},
                    }
                ],
            },
        }
        for tick in planning
    ]
    write_ndjson(directory / "observations.ndjson", observations)
    (directory / "results.json").write_text(
        json.dumps({"tick": 900, "scores": [20, 10], "winner": 0})
    )
    return directory


def command(
    cutoff: int,
    *,
    accepted=True,
    receipt=None,
    reason="accepted",
    probabilities=None,
    claim="Observed activity",
    run_id="run",
) -> dict:
    receipt = cutoff if receipt is None else receipt
    return {
        "receipt_tick": receipt,
        "command": {
            "type": "assessment",
            "schema_version": 1,
            "run_id": run_id,
            "request_id": f"r{cutoff}",
            "cutoff_tick": cutoff,
            "expiry_tick": cutoff + 450,
            "assessment": {
                "hypotheses": [
                    {"claim": claim, "alternative": "Insufficient evidence", "evidence_ids": ["e1"]}
                ],
                "forecast": {
                    "horizon_tick": cutoff + 300,
                    "probabilities": probabilities or [0.7, 0.1, 0.1, 0.1],
                },
                "assignments": [{"bot_id": 0, "objective_id": 0}],
            },
        },
        "result": {
            "accepted": accepted,
            "reason": reason,
            "application_tick": receipt + 1,
            "remaining_horizon_ticks": cutoff + 300 - receipt - 1,
        },
    }


def test_forecast_scoring_uses_truth_ties_and_all_opportunities(tmp_path):
    run = recorded_run(tmp_path)
    write_ndjson(
        run / "commands.ndjson",
        [command(0), command(300, accepted=False, reason="unknown evidence id")],
    )
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "mode": "two",
                "request_id": "r0",
                "latency_seconds": 0.2,
                "usage": [{"cost_usd": 0.01, "input_tokens": 10, "output_tokens": 20}],
            }
        ],
    )
    summary = summarize_run(run)
    assert summary["planning_opportunities"] == 3
    assert summary["forecast_coverage"] == pytest.approx(1 / 3)
    assert summary["forecast_status_counts"] == {"scored": 1, "rejected": 1, "missing": 1}
    assert summary["forecasts"][0]["outcome"] == 0
    assert summary["forecast_accuracy"] == 1
    assert summary["brier_score"] == pytest.approx(0.12)
    assert summary["evidence_errors"] == 1
    assert summary["outcome"] == "win"
    assert summary["cost_usd"] == pytest.approx(0.01)
    assert summary["usage"]["output_tokens"] == 20
    assert summary["application_latency_seconds"]["mean"] == 0.1


def test_superseded_forecast_is_uncovered_not_silently_removed(tmp_path):
    run = recorded_run(tmp_path, planning=(0, 100))
    write_ndjson(run / "commands.ndjson", [command(0), command(100)])
    summary = summarize_run(run)
    assert summary["planning_opportunities"] == 2
    assert summary["forecast_status_counts"] == {"superseded": 1, "scored": 1}
    assert summary["forecasts"][0]["superseded_tick"] == 101
    assert summary["forecast_coverage"] == 0.5


def test_none_outcome_and_rejected_duplicate_preserve_coverage(tmp_path):
    run = recorded_run(tmp_path, planning=(0,))
    states = [json.loads(line) for line in (run / "states.ndjson").read_text().splitlines()]
    for record in states:
        for bot in record["state"]["bots"]:
            if bot["team"] == 1:
                bot.update(x=31, y=0)
    write_ndjson(run / "states.ndjson", states)
    write_ndjson(
        run / "commands.ndjson",
        [
            command(0, probabilities=[0, 0, 0, 1]),
            command(0, accepted=False, reason="duplicate request"),
        ],
    )
    summary = summarize_run(run)
    assert summary["forecasts"][0]["outcome"] == 3
    assert summary["brier_score"] == 0
    assert summary["scored_forecasts"] == 1
    assert summary["rejected_assessments"] == 1


def test_late_missing_and_unavailable_forecasts_have_explicit_status(tmp_path):
    run = recorded_run(tmp_path)
    write_ndjson(
        run / "commands.ndjson",
        [command(0, accepted=False, reason="late response", receipt=250), command(300)],
    )
    states = [json.loads(line) for line in (run / "states.ndjson").read_text().splitlines()]
    write_ndjson(run / "states.ndjson", [record for record in states if record["tick"] != 600])
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r600",
                "status": "timeout",
                "error": "cycle deadline exceeded",
                "latency_seconds": 20,
            }
        ],
    )
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {
        "rejected": 1,
        "horizon_unavailable": 1,
        "missing": 1,
    }
    assert summary["forecast_accuracy"] is None
    assert summary["stale_result_rate"] == 0.5
    assert summary["forecasts"][2]["reason"] == "cycle deadline exceeded"


def test_invalid_forecast_is_not_scored_even_if_log_claims_acceptance(tmp_path):
    run = recorded_run(tmp_path, planning=(0,))
    record = command(0)
    record["command"]["assessment"]["forecast"]["probabilities"] = [0.4, 0.4, 0.4, 0.4]
    write_ndjson(run / "commands.ndjson", [record])
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"invalid_forecast": 1}
    assert summary["scored_forecasts"] == 0


def test_report_aggregates_counts_and_limits_offline_claims(tmp_path):
    first = recorded_run(tmp_path, name="first")
    second = recorded_run(tmp_path, planning=(0,), name="second")
    write_ndjson(first / "commands.ndjson", [command(0, run_id="first")])
    write_ndjson(second / "commands.ndjson", [command(0, run_id="second")])
    result = summarize_tournament([first, second])
    group = result["configurations"]["scripted/scripted"]
    assert group["runs"] == 2
    assert group["planning_opportunities"] == 4
    assert group["forecast_coverage"] == 0.5
    assert group["accuracy_wilson_95"] is not None
    output = write_report([first, second], tmp_path / "report.md")
    text = output.read_text()
    assert "2 / 4" in text
    assert "do not establish an improvement from LLM reasoning" in text
    assert "within-match correlation" in text


def test_viewer_is_portable_safe_and_preserves_opponent_snapshot_boundary(tmp_path):
    run = recorded_run(tmp_path, planning=(0,))
    injected_claim = '</script><script>alert("claim")</script>'
    write_ndjson(run / "commands.ndjson", [command(0, claim=injected_claim)])
    output = generate_viewer(run)
    text = output.read_text()
    assert output == run / "replay.html"
    assert injected_claim not in text
    assert "\\u003c/script\\u003e" in text
    assert "innerHTML" not in text
    assert "<script src=" not in text
    assert "secret_policy" not in text
    encoded = text.split('<script id="replay-data" type="application/json">', 1)[1].split(
        "</script>", 1
    )[0]
    data = json.loads(encoded)
    assert data["commands"][0]["command"]["assessment"]["hypotheses"][0]["claim"] == injected_claim
    assert data["observations"][0]["observation"]["visible_opponents"] == []
    assert len(data["states"][0]["bots"]) == 3
    public = data["states"][0]["public_telemetry"]
    assert [bot["id"] for bot in public["bots"]] == [0]
    assert public["scores"] == [20, 10]
    assert "opponent reports retain their original timestamps" in text
    assert "Evaluator ground truth" in text


def test_live_public_telemetry_tracks_friendlies_without_opponent_positions():
    state = {
        "tick": 17,
        "scores": [4, 9],
        "bots": [
            {"id": 0, "team": 0, "x": 2, "y": 3, "objective_id": 1},
            {"id": 1, "team": 1, "x": 22, "y": 23, "objective_id": 2},
        ],
        "opponent_policy": "holder",
    }
    initial = _public_telemetry(state, 0)
    state["bots"][1]["x"] = 25
    assert _public_telemetry(state, 0) == initial
    state["bots"][0]["x"] = 3
    assert _public_telemetry(state, 0)["bots"][0]["x"] == 3
    assert "opponent_policy" not in initial
    assert [bot["id"] for bot in _public_telemetry(state, 1)["bots"]] == [1]


def test_malformed_record_fails_instead_of_silently_changing_metrics(tmp_path):
    run = recorded_run(tmp_path)
    (run / "commands.ndjson").write_text('{"receipt_tick":\n')
    with pytest.raises(ValueError, match="commands.ndjson:1"):
        summarize_run(run)


def test_live_backend_classification_and_uncertain_cost_are_explicit(tmp_path):
    run = recorded_run(tmp_path, planning=(0,))
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "multi",
                "mode": "multi",
                "request_id": "r0",
                "status": "timeout",
                "latency_seconds": 20,
                "usage": [{"status": "uncertain", "reservation_id": "reservation"}],
            }
        ],
    )
    summary = summarize_run(run)
    assert summary["configuration"] == "multi/two"
    assert summary["measurement_kind"] == "live_api"
    assert summary["uncertain_cost_reservations"] == 1
    assert summary["cost_complete"] is False
    assert summary["forecast_status_counts"] == {"missing": 1}


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "malformed",
        {"forecast": None},
        {"forecast": {"horizon_tick": 300, "probabilities": None}},
    ],
)
def test_malformed_rejected_payload_remains_reportable(tmp_path, invalid):
    run = recorded_run(tmp_path, planning=(0,))
    record = command(0, accepted=False, reason="invalid assessment structure")
    record["command"]["assessment"] = invalid
    write_ndjson(run / "commands.ndjson", [record])
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"rejected": 1}
    assert generate_viewer(run).exists()


def test_supersession_at_exact_horizon_invalidates_original_condition(tmp_path):
    run = recorded_run(tmp_path, planning=(0, 299))
    write_ndjson(run / "commands.ndjson", [command(0), command(299)])
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"superseded": 1, "scored": 1}
    assert summary["forecasts"][0]["superseded_tick"] == 300
    assert summary["forecast_coverage"] == 0.5


def test_expiry_at_exact_horizon_is_uncovered(tmp_path):
    run = recorded_run(tmp_path, planning=(0,))
    record = command(0)
    record["command"]["expiry_tick"] = 300
    write_ndjson(run / "commands.ndjson", [record])
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"expired": 1}
    assert summary["forecast_accuracy"] is None


@pytest.mark.parametrize(
    "probabilities", [[], [True, 0, 0, 0], [float("nan"), 0, 0, 0], [-1, 1, 1, 0]]
)
def test_empty_or_illegal_probability_vector_cannot_be_scored(tmp_path, probabilities):
    run = recorded_run(tmp_path, planning=(0,))
    record = command(0)
    record["command"]["assessment"]["forecast"]["probabilities"] = probabilities
    write_ndjson(run / "commands.ndjson", [record])
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"invalid_forecast": 1}
    assert summary["forecast_coverage"] == 0


def test_host_evidence_errors_are_counted_and_busy_is_not_an_inference(tmp_path):
    run = recorded_run(tmp_path)
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r0",
                "status": "invalid",
                "validation_category": "evidence",
                "latency_seconds": 2,
            },
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r300",
                "status": "busy",
                "latency_seconds": 0,
            },
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r600",
                "status": "timeout",
                "latency_seconds": 20,
            },
        ],
    )
    summary = summarize_run(run)
    assert summary["evidence_errors"] == 1
    assert summary["host_validation_errors"] == {"evidence": 1}
    assert summary["inference_latency_seconds"]["count"] == 2
    assert summary["inference_latency_seconds"]["mean"] == 11
    assert summary["planning_opportunities"] == 3
    assert summary["forecast_status_counts"] == {"missing": 3}


def test_game_outcome_respects_reasoner_team_and_score_ties(tmp_path):
    run = recorded_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["config"]["reasoner_team"] = 1
    (run / "manifest.json").write_text(json.dumps(manifest))
    assert summarize_run(run)["outcome"] == "loss"
    (run / "results.json").write_text(json.dumps({"tick": 900, "scores": [10, 10], "winner": None}))
    assert summarize_run(run)["outcome"] == "draw"


def fallback(cutoff, receipt, *, accepted=True, reason="timeout"):
    return {
        "receipt_tick": receipt,
        "command": {
            "type": "fallback",
            "schema_version": 1,
            "run_id": "run",
            "request_id": f"r{cutoff}",
            "cutoff_tick": cutoff,
            "reason": reason,
        },
        "result": {
            "type": "fallback_result",
            "accepted": accepted,
            "application_tick": receipt + 1,
            "reason": "accepted" if accepted else reason,
        },
    }


def test_failure_fallback_cancels_forecast_without_becoming_a_forecast(tmp_path):
    run = recorded_run(tmp_path, planning=(0, 100))
    write_ndjson(run / "commands.ndjson", [command(0), fallback(100, 101)])
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r100",
                "cutoff_tick": 100,
                "status": "timeout",
                "latency_seconds": 20,
            }
        ],
    )
    summary = summarize_run(run)
    assert summary["planning_opportunities"] == 2
    assert summary["forecast_status_counts"] == {"superseded": 1, "missing": 1}
    assert summary["forecasts"][0]["superseded_by_type"] == "fallback"
    assert summary["forecasts"][0]["superseded_tick"] == 102
    assert summary["forecasts"][1]["fallback"]["accepted"] is True
    assert summary["accepted_assessments"] == 1
    assert summary["accepted_fallbacks"] == 1
    assert summary["rejected_assessments"] == 0
    assert summary["evidence_errors"] == 0
    assert summary["forecast_coverage"] == 0
    viewer = generate_viewer(run).read_text()
    encoded = viewer.split('<script id="replay-data" type="application/json">', 1)[1]
    data = json.loads(encoded.split("</script>", 1)[0])
    assert data["commands"][1]["command"]["type"] == "fallback"
    assert data["failures"] == []  # Native fallback supplies the precise event timing.
    assert "Nearest-objective fallback cleared preceding advice" in viewer


def test_rejected_fallback_does_not_clear_advice_or_count_as_assessment_error(tmp_path):
    run = recorded_run(tmp_path, planning=(0, 100))
    write_ndjson(
        run / "commands.ndjson",
        [
            command(0),
            fallback(100, 101, accepted=False, reason="unknown evidence failure"),
        ],
    )
    summary = summarize_run(run)
    assert summary["forecast_status_counts"] == {"scored": 1, "missing": 1}
    assert summary["rejected_fallbacks"] == 1
    assert summary["rejected_assessments"] == 0
    assert summary["evidence_errors"] == 0
    assert summary["stale_results"] == 0


def test_fallback_on_horizon_cancels_forecast_and_missing_host_event_is_visible(tmp_path):
    run = recorded_run(tmp_path, planning=(0, 100, 600))
    write_ndjson(run / "commands.ndjson", [command(0), fallback(100, 299)])
    write_ndjson(
        run / "reasoning.ndjson",
        [
            {
                "type": "reasoning_cycle",
                "backend": "mock",
                "request_id": "r600",
                "cutoff_tick": 600,
                "status": "busy",
                "latency_seconds": 0,
            }
        ],
    )
    summary = summarize_run(run)
    assert summary["forecasts"][0]["status"] == "superseded"
    assert summary["forecasts"][0]["superseded_tick"] == 300
    viewer = generate_viewer(run).read_text()
    encoded = viewer.split('<script id="replay-data" type="application/json">', 1)[1]
    data = json.loads(encoded.split("</script>", 1)[0])
    assert data["failures"][0]["status"] == "busy"
    assert data["failures"][0]["visible_tick"] == 600
