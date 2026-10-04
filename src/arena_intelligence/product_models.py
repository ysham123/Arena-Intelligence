"""Public simulation-only integration contract; no external control commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .protocol import Assignment, Forecast, Hypothesis


class ProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ConnectionCreate(ProductModel):
    name: str = Field(min_length=1, max_length=120)
    adapter: Literal["simulation-v1"] = "simulation-v1"
    description: str = Field(default="", max_length=500)


class ConnectionUpdate(ProductModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class SessionInput(ProductModel):
    external_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=120)
    status: Literal["running", "completed", "stopped"] = "running"


class Entity(ProductModel):
    id: str = Field(min_length=1, max_length=128)
    x: float = Field(ge=-1e6, le=1e6)
    y: float = Field(ge=-1e6, le=1e6)
    label: str | None = Field(default=None, max_length=120)
    team: str | None = Field(default=None, max_length=80)
    source_time: float | None = Field(default=None, ge=0, le=1e12)
    objective_id: int | None = Field(default=None, ge=0, le=1000000)


class World(ProductModel):
    width: float = Field(gt=0, le=1e6)
    height: float = Field(gt=0, le=1e6)
    blocked: list[list[int]] = Field(default_factory=list, max_length=1024)
    nodes: list[dict[str, float]] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_map(self):
        if any(len(cell) != 2 for cell in self.blocked):
            raise ValueError("blocked cells need coordinate pairs")
        if any(set(node) != {"id", "x", "y"} for node in self.nodes):
            raise ValueError("world nodes need id, x and y")
        return self


class Snapshot(ProductModel):
    entities: list[Entity] = Field(max_length=128)
    world: World | None = None
    scores: list[float] | None = Field(default=None, max_length=8)
    visibility: Literal["reported", "observer", "evaluator"] = "reported"
    tick: int | None = Field(default=None, ge=0, le=36000)
    agent_cutoff_time: float | None = Field(default=None, ge=0, le=1e12)

    @model_validator(mode="after")
    def unique_entities(self):
        if len({e.id for e in self.entities}) != len(self.entities):
            raise ValueError("snapshot entity IDs must be unique")
        return self


class PublicEvidence(ProductModel):
    id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=80)
    observed_at: float = Field(ge=0, le=1e12)
    received_at: float = Field(ge=0, le=1e12)
    summary: str = Field(min_length=1, max_length=1000)
    entity_id: str | None = Field(default=None, max_length=128)
    details: dict[str, float] = Field(default_factory=dict, max_length=6)

    @model_validator(mode="after")
    def public_times_and_fields(self):
        if self.received_at < self.observed_at:
            raise ValueError("evidence cannot arrive before its observation")
        if set(self.details) - {"node_id", "friendly_count", "opponent_count", "bot_id", "x", "y"}:
            raise ValueError("evidence details contain unsupported public fields")
        if any(not -1e6 <= value <= 1e6 for value in self.details.values()):
            raise ValueError("evidence numeric value outside bounds")
        return self


class EvidencePayload(ProductModel):
    records: list[PublicEvidence] = Field(max_length=128)
    available_source_time: float | None = Field(default=None, ge=0, le=1e12)


class AssessmentPayload(ProductModel):
    status: str = Field(min_length=1, max_length=32, pattern=r"^[a-z_]+$")
    origin: Literal["model", "scripted", "imported"] = "imported"
    validation_scope: Literal["schema_and_received_evidence_references_only"] = (
        "schema_and_received_evidence_references_only"
    )
    assignments: list[Assignment] = Field(default_factory=list, max_length=128)
    summary: str = Field(default="", max_length=2000)
    request_id: str | None = Field(default=None, max_length=128)
    cutoff_tick: int | None = Field(default=None, ge=0, le=36000)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)
    forecast: Forecast | None = None
    latency_seconds: float | None = Field(default=None, ge=0, le=600)
    cost_usd: float | None = Field(default=None, ge=0, le=55)
    accepted: bool | None = None
    application_source_time: float | None = Field(default=None, ge=0, le=1e12)
    expiry_source_time: float | None = Field(default=None, ge=0, le=1e12)


class Metric(ProductModel):
    name: str = Field(min_length=1, max_length=120)
    value: float = Field(ge=-1e12, le=1e12)
    unit: str | None = Field(default=None, max_length=40)


class Lifecycle(ProductModel):
    status: Literal["running", "completed", "stopped", "failed"]
    message: str = Field(default="", max_length=1000)


PAYLOAD_MODELS = {
    "snapshot": Snapshot,
    "evidence": EvidencePayload,
    "assessment": AssessmentPayload,
    "metric": Metric,
    "lifecycle": Lifecycle,
}


class TelemetryEvent(ProductModel):
    sequence: int = Field(ge=0, le=2**63 - 1)
    source_time: float = Field(ge=0, le=1e12)
    type: Literal["snapshot", "evidence", "assessment", "metric", "lifecycle"]
    payload: dict

    @model_validator(mode="after")
    def validated_payload(self):
        parsed = PAYLOAD_MODELS[self.type].model_validate(self.payload)
        self.payload = parsed.model_dump(exclude_none=True)
        if self.type == "snapshot" and any(
            e.get("source_time", self.source_time) > self.source_time
            for e in self.payload["entities"]
        ):
            raise ValueError("snapshot cannot contain future entity positions")
        if self.type == "evidence" and any(
            r["received_at"] > self.source_time for r in self.payload["records"]
        ):
            raise ValueError("evidence event contains undelivered reports")
        return self


class TelemetryBatch(ProductModel):
    schema_version: Literal[1] = 1
    scope: Literal["synthetic_simulation"]
    session: SessionInput
    events: list[TelemetryEvent] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def ordered_batch(self):
        sequences = [e.sequence for e in self.events]
        if sequences != sorted(set(sequences)):
            raise ValueError("event sequences must be unique and increasing")
        times = [e.source_time for e in self.events]
        if times != sorted(times):
            raise ValueError("source times must be nondecreasing")
        return self


class DemoRequest(ProductModel):
    name: str = Field(default="Reference arena", min_length=1, max_length=120)
    mode: Literal["recorded", "native"] = "recorded"
    backend: Literal["mock", "frequency", "single", "multi"] = "mock"
    allow_paid: bool = False
