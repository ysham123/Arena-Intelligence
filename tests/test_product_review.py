from copy import deepcopy

from arena_intelligence.product_review import build_review, compare_sessions


def recorded(session_id="a", fingerprint="same-scenario"):
    return {
        "session": {"id": session_id, "name": "Recorded test", "status": "completed"},
        "replay": {
            "config": {"tick_hz": 10, "reasoner_team": 0},
            "comparison_context": {"scenario_fingerprint": fingerprint},
            "summary": {
                "forecast_coverage": 1,
                "forecast_accuracy": 0,
                "brier_score": 1.6992,
                "planning_opportunities": 1,
                "scored_forecasts": 1,
                "cost_usd": 0.04,
            },
            "frames": [{"scores": [262, 169]}],
            "forecasts": [
                {
                    "request_id": "r3",
                    "status": "scored",
                    "cutoff_tick": 900,
                    "horizon_tick": 1200,
                    "probabilities": [0.04, 0.04, 0.88, 0.04],
                    "prediction": 2,
                    "outcome": 1,
                    "correct": False,
                    "remaining_horizon_ticks": 164,
                    "latency_seconds": 13.6,
                }
            ],
            "commands": [
                {
                    "command": {
                        "type": "assessment",
                        "request_id": "r3",
                        "assessment": {
                            "hypotheses": [
                                {
                                    "claim": "Reported concentration at node 2.",
                                    "evidence_ids": ["e1", "e2"],
                                    "alternative": "The group may have moved since observation.",
                                }
                            ]
                        },
                    }
                }
            ],
        },
    }


def test_confident_miss_retains_original_assessment_and_evaluation_boundary():
    result = build_review([recorded()])
    assert result["counts"] == {"high": 1, "medium": 0, "info": 0}
    issue = result["items"][0]
    assert "88%" in issue["title"]
    assert "node 1" in issue["summary"]
    assert issue["evidence_ids"] == ["e1", "e2"]
    assert issue["request_id"] == "r3"
    assert issue["alternatives"] == ["The group may have moved since observation."]
    assert "not available to the agent" in issue["evaluation_boundary"]


def test_low_probability_miss_does_not_receive_high_priority():
    detail = recorded()
    detail["replay"]["forecasts"][0]["probabilities"] = [0.25, 0.25, 0.3, 0.2]
    assert build_review([detail])["counts"]["high"] == 0
    assert build_review([detail])["counts"]["info"] == 1


def test_uncovered_forecast_retained_and_cannot_be_marked_as_miss():
    detail = recorded()
    detail["replay"]["forecasts"][0]["status"] = "superseded"
    issues = build_review([detail])["items"]
    assert len(issues) == 1
    assert issues[0]["kind"] == "forecast_uncovered"
    assert issues[0]["priority"] == "medium"


def test_horizon_timing_is_a_separate_issue():
    detail = recorded()
    detail["replay"]["forecasts"][0]["remaining_horizon_ticks"] = 100
    issues = build_review([detail])["items"]
    assert {issue["kind"] for issue in issues} == {"forecast_miss", "reduced_horizon"}
    assert len({issue["id"] for issue in issues}) == 2


def test_comparison_requires_matching_scenario_and_reports_denominators():
    left, right = recorded(), recorded("b")
    right["replay"]["summary"]["forecast_accuracy"] = 1
    comparison = compare_sessions(left, right)
    assert comparison["comparable"]
    rows = {row["key"]: row for row in comparison["rows"]}
    assert rows["accuracy"]["delta"] == 100
    assert rows["scored"]["left"] == 1
    assert rows["opportunities"]["left"] == 1
    assert rows["latency"]["left"] == 13.6
    assert rows["score"]["left"] == 262
    assert "matched-input" in " ".join(comparison["limitations"])
    right["replay"]["comparison_context"]["scenario_fingerprint"] = "different"
    assert not compare_sessions(left, right)["comparable"]
    assert not compare_sessions(left, deepcopy(left))["comparable"]


def test_external_stream_does_not_invent_native_measurements():
    stream = {"session": {"id": "stream", "name": "External stream"}, "events": []}
    comparison = compare_sessions(recorded(), stream)
    assert not comparison["comparable"]
    assert all(row["right"] is None and row["delta"] is None for row in comparison["rows"])
    assert build_review([stream])["items"] == []


def test_legacy_reference_fingerprint_and_invalid_context_are_handled():
    left, right = recorded(), recorded("b")
    left["replay"]["comparison_context"] = "a" * 64
    right["replay"]["comparison_context"] = {"scenario_fingerprint": "a" * 64}
    assert compare_sessions(left, right)["comparable"]
    right["replay"]["comparison_context"] = ["invalid"]
    assert not compare_sessions(left, right)["comparable"]
