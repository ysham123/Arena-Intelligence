import copy

import pytest
from pydantic import ValidationError

from arena_intelligence.evidence import run_check
from arena_intelligence.protocol import (
    CheckRequest,
    ObservationEnvelope,
    validate_assessment,
)
from arena_intelligence.reasoner import frequency_assessment


def test_strict_public_observation_blocks_hidden_fields(observation):
    data = observation.model_dump()
    data["observation"]["opponent_policy"] = "holder"
    with pytest.raises(ValidationError):
        ObservationEnvelope.model_validate(data)
    data = observation.model_dump()
    data["observation"]["evidence"][0]["data"]["hidden_cause"] = 1
    with pytest.raises(ValidationError):
        ObservationEnvelope.model_validate(data)


def test_validation_rejects_unknown_evidence_and_duplicate_assignments(observation):
    assessment = frequency_assessment(observation, [observation])
    valid = validate_assessment(assessment, observation, {"e0"})
    assert valid.expiry_tick == 550
    invalid = copy.deepcopy(assessment)
    invalid.hypotheses[0].evidence_ids = ["hidden-evidence"]
    with pytest.raises(ValueError, match="unknown"):
        validate_assessment(invalid, observation, {"e0"})
    invalid = copy.deepcopy(assessment)
    invalid.assignments.append(invalid.assignments[0])
    with pytest.raises(ValueError, match="exactly once"):
        validate_assessment(invalid, observation, {"e0"})


def test_nonfinite_and_wrong_horizon_are_rejected(observation):
    assessment = frequency_assessment(observation, [observation])
    data = assessment.model_dump()
    data["forecast"]["probabilities"] = [float("nan"), 0.0, 0.0, 0.0]
    with pytest.raises(ValidationError):
        type(assessment).model_validate(data)
    assessment.forecast.horizon_tick = 401
    with pytest.raises(ValueError, match="horizon"):
        validate_assessment(assessment, observation, {"e0"})


def test_checks_deduplicate_delayed_reports_and_block_future_reads(observation):
    check = CheckRequest(kind="objective_visits", window_start_tick=0, window_end_tick=100)
    result = run_check(check, [observation, observation], 100)
    assert result["observed_report_counts"] == {"0": 1, "1": 0, "2": 0}
    timing = run_check(
        CheckRequest(kind="report_timing", window_start_tick=0, window_end_tick=100),
        [observation],
        100,
    )
    assert timing["mean_delay_ticks"] == 20
    check.window_end_tick = 101
    with pytest.raises(ValueError, match="cutoff"):
        run_check(check, [observation], 100)
