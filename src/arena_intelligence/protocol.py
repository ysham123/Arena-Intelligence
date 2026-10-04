"""Strict public wire types and deterministic assessment validation."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunConfig(WireModel):
    seed: int = Field(default=42, ge=0, le=2**64 - 1)
    bots_per_team: int = Field(default=6, ge=1, le=128)
    duration_ticks: int = Field(default=1800, ge=1, le=36000)
    tick_hz: Literal[10] = 10
    planning_ticks: list[int] = Field(
        default_factory=lambda: [0, 300, 600, 900, 1200, 1500], max_length=128
    )
    opponent_policy: Literal["nearest", "holder", "switch"] = "nearest"
    reasoner_team: Literal[0, 1] = 0
    mirrored: bool = False
    report_drop_rate: float = Field(default=0.1, ge=0, le=1)
    report_delay_ticks: int = Field(default=20, ge=0, le=300)
    paced: bool = False

    @field_validator("tick_hz", "reasoner_team", mode="before")
    @classmethod
    def integer_constants(cls, value):
        if type(value) is not int:
            raise ValueError("native configuration constants must be integers")
        return value

    @model_validator(mode="after")
    def ordered_planning(self) -> RunConfig:
        if self.planning_ticks != sorted(set(self.planning_ticks)):
            raise ValueError("planning ticks must be unique and increasing")
        if any(t < 0 or t >= self.duration_ticks for t in self.planning_ticks):
            raise ValueError("planning ticks must be within the match")
        return self


class Node(WireModel):
    id: Literal[0, 1, 2]
    x: int
    y: int


class Friendly(WireModel):
    id: int
    team: Literal[0, 1]
    x: int
    y: int
    objective_id: Literal[0, 1, 2]


class ReportedOpponent(WireModel):
    id: int
    team: Literal[0, 1]
    x: int
    y: int
    observed_tick: int
    evidence_id: str


class Evidence(WireModel):
    id: str = Field(min_length=1, max_length=128)
    tick: int = Field(ge=0)
    kind: Literal["sighting", "node_status"]
    data: dict[str, int]

    @model_validator(mode="after")
    def permitted_fields(self) -> Evidence:
        expected = {
            "sighting": {"bot_id", "x", "y", "observed_tick", "delivered_tick"},
            "node_status": {
                "node_id",
                "friendly_count",
                "opponent_count",
                "observed_tick",
                "delivered_tick",
            },
        }[self.kind]
        if set(self.data) != expected:
            raise ValueError("evidence contains unknown or missing public fields")
        if self.tick != self.data["observed_tick"]:
            raise ValueError("evidence tick must identify the source observation")
        if self.data["delivered_tick"] < self.data["observed_tick"]:
            raise ValueError("a report cannot arrive before it was observed")
        return self


class Observation(WireModel):
    tick: int = Field(ge=0)
    team: Literal[0, 1]
    size: int = Field(ge=1)
    scores: list[int] = Field(min_length=2, max_length=2)
    nodes: list[Node] = Field(min_length=3, max_length=3)
    blocked: list[list[int]]
    friendly: list[Friendly]
    visible_opponents: list[ReportedOpponent]
    last_seen: list[ReportedOpponent]
    evidence: list[Evidence] = Field(max_length=128)

    @model_validator(mode="after")
    def public_consistency(self) -> Observation:
        if {n.id for n in self.nodes} != {0, 1, 2}:
            raise ValueError("expected three distinct nodes")
        if len({b.id for b in self.friendly}) != len(self.friendly):
            raise ValueError("duplicate friendly ID")
        if any(b.team != self.team for b in self.friendly):
            raise ValueError("friendly team mismatch")
        if len({e.id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence ID")
        if any(e.tick > self.tick for e in self.evidence):
            raise ValueError("future evidence")
        if any(e.data["delivered_tick"] > self.tick for e in self.evidence):
            raise ValueError("undelivered future report")
        if any(r.observed_tick > self.tick for r in self.visible_opponents + self.last_seen):
            raise ValueError("future opponent report")
        if any(len(cell) != 2 for cell in self.blocked):
            raise ValueError("blocked cells require coordinate pairs")
        return self


class ObservationEnvelope(WireModel):
    type: Literal["observation"] = "observation"
    schema_version: Literal[1] = 1
    run_id: str
    request_id: str
    cutoff_tick: int
    observation: Observation

    @model_validator(mode="after")
    def matching_tick(self) -> ObservationEnvelope:
        if self.cutoff_tick != self.observation.tick:
            raise ValueError("cutoff must match observation tick")
        return self


class Hypothesis(WireModel):
    claim: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(max_length=32)
    alternative: str = Field(min_length=1, max_length=1200)


class Forecast(WireModel):
    horizon_tick: int
    probabilities: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def distribution(self) -> Forecast:
        if any(not math.isfinite(p) or p < 0 or p > 1 for p in self.probabilities):
            raise ValueError("probabilities must be finite and within [0,1]")
        if abs(sum(self.probabilities) - 1) > 1e-6:
            raise ValueError("probabilities must sum to one")
        return self


class Assignment(WireModel):
    bot_id: int
    objective_id: Literal[0, 1, 2]


class Assessment(WireModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    forecast: Forecast
    assignments: list[Assignment]


class CheckRequest(WireModel):
    kind: Literal["objective_visits", "occupancy_trends", "report_timing"]
    window_start_tick: int = Field(ge=0)
    window_end_tick: int = Field(ge=0)


class AnalystDraft(Assessment):
    checks: list[CheckRequest] = Field(max_length=3)


class AssessmentEnvelope(WireModel):
    type: Literal["assessment"] = "assessment"
    schema_version: Literal[1] = 1
    run_id: str
    request_id: str
    cutoff_tick: int
    expiry_tick: int
    assessment: Assessment


class AssessmentValidationError(ValueError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def validate_assessment(
    assessment: Assessment,
    request: ObservationEnvelope,
    known_evidence: set[str],
) -> AssessmentEnvelope:
    """Validate references and legality, never imply free-text claims are proven."""
    if assessment.forecast.horizon_tick != request.cutoff_tick + 300:
        raise AssessmentValidationError("horizon", "forecast horizon must equal cutoff + 300")
    expected = {b.id for b in request.observation.friendly}
    assigned = [a.bot_id for a in assessment.assignments]
    if set(assigned) != expected or len(assigned) != len(expected):
        raise AssessmentValidationError(
            "assignment", "assignments must cover each friendly exactly once"
        )
    for h in assessment.hypotheses:
        if any(e not in known_evidence for e in h.evidence_ids):
            raise AssessmentValidationError("evidence", "unknown permitted evidence reference")
        if not h.evidence_ids and not h.claim.lower().startswith(("prior:", "insufficient")):
            raise AssessmentValidationError(
                "evidence", "an unsupported claim must explicitly identify insufficient evidence"
            )
    return AssessmentEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        cutoff_tick=request.cutoff_tick,
        expiry_tick=request.cutoff_tick + 450,
        assessment=assessment,
    )


def envelope(kind: str, run_id: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "schema_version": 1, "run_id": run_id, **fields}
