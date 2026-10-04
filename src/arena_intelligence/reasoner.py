"""Bounded analyst/check/challenger reasoning with no privileged file access."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from .budget import NANO_PER_USD, Bucket, BudgetExceeded, CostLedger, worst_case_cost
from .context import (
    CONTEXT_VERSION,
    compact_check_summary,
    compact_context,
    encode_prompt,
    initial_check_summaries,
    permitted_records,
)
from .evidence import run_check
from .protocol import (
    AnalystDraft,
    Assessment,
    Assignment,
    CheckRequest,
    Forecast,
    Hypothesis,
    ObservationEnvelope,
    validate_assessment,
)

MODEL = "claude-sonnet-5-5"
Backend = Literal["mock", "single", "multi", "frequency"]
SYSTEM = """You assess a fictional resource-control game from permitted, delayed observations.
Return only the requested structured result. Hypotheses are uncertain inferences,
not established facts. Cite exact permitted evidence IDs. State a competing explanation.
With no supporting evidence begin the claim with 'Insufficient evidence:' or 'Prior:'.
Never infer an opponent policy label or unseen current positions. Missing reports do
not imply an empty cell. A node_status opponent_count is a sensor-visible lower bound;
zero is not proof of an empty objective. Do not request hidden state, files, shell commands,
or networks.
Forecast the node with the largest opposing population within Manhattan distance 2 at
cutoff+300 (ties pick smallest ID; none if all empty). Emit four probabilities summing
to one. Assign every friendly exactly once to one of nodes 0,1,2. Give a short summary
of the hypothesis and alternative; do not provide private step-by-step reasoning.
Local checks, if requested, must use windows no later than the current cutoff."""


@dataclass
class CycleSpend:
    ceiling_nano: int = 5 * NANO_PER_USD
    amounts: list[int] = field(default_factory=list)

    def reserve(self, amount_nano: int) -> int:
        if sum(self.amounts) + amount_nano > self.ceiling_nano:
            raise BudgetExceeded("common $5 per-cycle spending ceiling would be exceeded")
        self.amounts.append(amount_nano)
        return len(self.amounts) - 1

    def settle(self, slot: int, amount_nano: int) -> None:
        self.amounts[slot] = amount_nano


_CURRENT_CYCLE: ContextVar[CycleSpend | None] = ContextVar("arena_cycle_spend", default=None)


def reasoning_config(backend: Backend) -> dict:
    live = backend in {"single", "multi"}
    return {
        "model": MODEL if live else None,
        "context_version": CONTEXT_VERSION,
        "initial_evidence_checks": 3,
        "effort": "medium" if live else None,
        "thinking": "between_tools" if live else None,
        "max_output_tokens_per_call": (8192 if backend == "single" else 4096) if live else 0,
        "max_calls_per_cycle": (1 if backend == "single" else 2) if live else 0,
        "total_output_token_allowance": 8192 if live else 0,
        "input_usd_per_million": 2 if live else 0,
        "output_usd_per_million": 10 if live else 0,
    }


class Provider(Protocol):
    async def produce(
        self,
        stage: str,
        prompt: str,
        output: type[BaseModel],
        max_tokens: int,
        run_id: str,
        usage: list[dict],
    ) -> BaseModel: ...


class ClaudeProvider:
    """Constructed only for explicitly selected paid backends in paced mode."""

    def __init__(self, ledger: CostLedger, bucket: Bucket, client: Any = None):
        if client is None:
            import anthropic

            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError("ANTHROPIC_API_KEY is required for a live backend")
            workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
            headers = {"anthropic-workspace-id": workspace} if workspace else {}
            client = anthropic.AsyncAnthropic(
                api_key=key,
                max_retries=0,
                timeout=20.0,
                base_url="https://api.anthropic.com",
                default_headers=headers,
            )
        self.client = client
        self.ledger = ledger
        self.bucket = bucket

    async def close(self) -> None:
        if hasattr(self.client, "close"):
            await self.client.close()

    async def produce(
        self,
        stage: str,
        prompt: str,
        output: type[BaseModel],
        max_tokens: int,
        run_id: str,
        usage: list[dict],
    ) -> BaseModel:
        from anthropic import APIStatusError, transform_schema

        schema = transform_schema(output)
        spend = _CURRENT_CYCLE.get()
        if spend is None:
            raise ValueError("paid requests require a bounded reasoning cycle")
        worst = worst_case_cost(
            len((SYSTEM + prompt).encode()), len(json.dumps(schema).encode()), max_tokens
        )
        slot = spend.reserve(worst)
        try:
            reservation = await asyncio.to_thread(
                self.ledger.reserve, self.bucket, run_id, stage, worst
            )
        except BaseException:
            # No model dispatch occurred. Any late DB reservation remains conservative
            # in the persistent ledger, but it is not API spending from this cycle.
            spend.settle(slot, 0)
            raise
        started = time.monotonic()
        settled = False
        try:
            response = await self.client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "between_tools"},
                output_config={
                    "effort": "medium",
                    "format": {
                        "type": "json_schema",
                        "schema": schema,
                    },
                },
            )
            cost = await asyncio.to_thread(
                self.ledger.settle,
                reservation,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            settled = True
            spend.settle(slot, round(cost * NANO_PER_USD))
            usage.append(
                {
                    "stage": stage,
                    "model": MODEL,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cost_usd": cost,
                    "reservation_id": reservation,
                    "duration_seconds": time.monotonic() - started,
                    "stop_reason": response.stop_reason,
                }
            )
            if response.stop_reason != "end_turn":
                raise ValueError("model response did not complete normally")
            text = "".join(block.text for block in response.content if block.type == "text")
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            usage[-1]["structured_output"] = (text.replace(key, "[REDACTED]") if key else text)[
                :16000
            ]
            if len(text.encode()) > 100_000:
                raise ValueError("model result exceeds bounded-output limit")
            return output.model_validate_json(text)
        except BaseException as error:
            if not settled:
                definitive_rejection = isinstance(error, APIStatusError) and error.status_code in {
                    400,
                    401,
                    403,
                    404,
                    413,
                    422,
                    429,
                }
                if definitive_rejection:
                    await asyncio.to_thread(self.ledger.settle, reservation, 0, 0)
                    spend.settle(slot, 0)
                else:
                    await asyncio.to_thread(self.ledger.mark_uncertain, reservation)
                usage.append(
                    {
                        "stage": stage,
                        "model": MODEL,
                        "reservation_id": reservation,
                        "status": "rejected_not_billed" if definitive_rejection else "uncertain",
                        "cost_usd": 0.0 if definitive_rejection else None,
                        "duration_seconds": time.monotonic() - started,
                    }
                )
            raise


def frequency_assessment(
    request: ObservationEnvelope, history: list[ObservationEnvelope]
) -> Assessment:
    """Laplace-smoothed sighting baseline; never counts absence as confirmed emptiness."""
    obs = request.observation
    nodes = {n.id: n for n in obs.nodes}
    counts: Counter = Counter({i: 1 for i in range(4)})
    seen: set[str] = set()
    supporting: list[str] = []
    for snapshot in history:
        for e in snapshot.observation.evidence:
            if e.kind != "sighting" or e.id in seen:
                continue
            seen.add(e.id)
            matches = [
                i for i, n in nodes.items() if abs(e.data["x"] - n.x) + abs(e.data["y"] - n.y) <= 2
            ]
            counts[min(matches) if matches else 3] += 1
            supporting.append(e.id)
    total = sum(counts.values())
    claim = (
        (
            "Received sightings suggest an objective-occupancy pattern; these reports "
            "do not establish the current hidden population."
        )
        if supporting
        else ("Insufficient evidence: use a uniform prior until opponent sightings arrive.")
    )
    assignments = [
        Assignment(
            bot_id=b.id,
            objective_id=min(
                nodes, key=lambda i: (abs(b.x - nodes[i].x) + abs(b.y - nodes[i].y), i)
            ),
        )
        for b in obs.friendly
    ]
    return Assessment(
        hypotheses=[
            Hypothesis(
                claim=claim,
                evidence_ids=supporting[-32:],
                alternative="Reporting delays or sampling bias may explain it.",
            )
        ],
        forecast=Forecast(
            horizon_tick=request.cutoff_tick + 300,
            probabilities=[counts[i] / total for i in range(4)],
        ),
        assignments=assignments,
    )


def mock_draft(request: ObservationEnvelope, history: list[ObservationEnvelope]) -> AnalystDraft:
    """A transparent exploratory heuristic, not a simulated LLM performance claim."""
    base = frequency_assessment(request, history)
    base.assignments = [
        Assignment(bot_id=bot.id, objective_id=index % 3)
        for index, bot in enumerate(sorted(request.observation.friendly, key=lambda b: b.id))
    ]
    latest = {}
    for snapshot in history:
        for record in snapshot.observation.evidence:
            if record.kind == "node_status":
                node_id = record.data["node_id"]
                old = latest.get(node_id)
                if old is None or (record.tick, record.id) > (old.tick, old.id):
                    latest[node_id] = record
    if latest:
        prominent = max(latest.values(), key=lambda r: (r.data["opponent_count"], r.tick))
        counts = [1 + latest[i].data["opponent_count"] if i in latest else 1 for i in range(3)]
        counts.append(1)
        base.forecast.probabilities = [count / sum(counts) for count in counts]
        base.hypotheses = [
            Hypothesis(
                claim=(
                    f"At source tick {prominent.tick}, the received report recorded "
                    f"{prominent.data['opponent_count']} sensor-visible opposing bots near node "
                    f"{prominent.data['node_id']}. Hypothesis: this objective's observed "
                    "concentration may persist into the forecast interval; "
                    "unseen bots elsewhere remain possible."
                ),
                evidence_ids=[prominent.id],
                alternative="Bots may move before the horizon, and received reports are delayed.",
            )
        ]
    return AnalystDraft(
        **base.model_dump(),
        checks=[
            CheckRequest(kind=kind, window_start_tick=0, window_end_tick=request.cutoff_tick)
            for kind in ("objective_visits", "occupancy_trends", "report_timing")
        ],
    )


class Reasoner:
    def __init__(
        self, backend: Backend, provider: Provider | None = None, mock_delay_seconds: float = 0
    ):
        if backend in {"single", "multi"} and provider is None:
            raise ValueError("live backends require a provider")
        self.backend = backend
        self.provider = provider
        self.mock_delay_seconds = mock_delay_seconds

    async def cycle(
        self,
        request: ObservationEnvelope,
        history: list[ObservationEnvelope],
        known_evidence: set[str],
        deadline_seconds: float = 20.0,
    ) -> dict:
        started = time.monotonic()
        spend = CycleSpend()
        context_token = _CURRENT_CYCLE.set(spend)
        log: dict = {
            "type": "reasoning_cycle",
            "backend": self.backend,
            "mode": self.backend,
            "model_config": reasoning_config(self.backend),
            "single_call_tradeoff": (
                "Both modes receive the same three initial check summaries. The single analyst's "
                "requested follow-up checks are executed and logged after its only call, so "
                "they cannot revise its answer. The multi challenger receives those results."
            )
            if self.backend == "single"
            else None,
            "request_id": request.request_id,
            "cutoff_tick": request.cutoff_tick,
            "status": "pending",
            "draft": None,
            "initial_checks": [],
            "checks": [],
            "prompt_bytes": {},
            "context_coverage": None,
            "challenger": None,
            "assessment": None,
            "usage": [],
            "validation_scope": "schema, permitted references, legality; "
            "free-text hypothesis truth is not deterministically proven",
        }
        try:
            async with asyncio.timeout(deadline_seconds):
                if self.backend == "mock" and self.mock_delay_seconds:
                    await asyncio.sleep(self.mock_delay_seconds)
                if any(
                    h.run_id != request.run_id or h.cutoff_tick > request.cutoff_tick
                    for h in history
                ):
                    raise ValueError("history must contain only this run's permitted past")
                # The current observation is incorporated exactly once in local history.
                # A caller may provide only prior snapshots or include the current one.
                history = [h for h in history if h.cutoff_tick != request.cutoff_tick] + [request]
                records = permitted_records(history, request.cutoff_tick)
                initial_checks = initial_check_summaries(history, request.cutoff_tick, records)
                log["initial_checks"] = initial_checks
                context = compact_context(request, history, records)
                log["context_coverage"] = context["coverage"]
                if self.backend == "frequency":
                    final = frequency_assessment(request, history)
                else:
                    if self.backend == "mock":
                        draft = mock_draft(request, history)
                    else:
                        prompt = encode_prompt(
                            {
                                "task": "Analyst: draft grounded hypotheses, at most three "
                                "selected follow-up checks, a distribution, and complete "
                                "objective assignments. Use the shared initial checked facts; "
                                "follow-up results become available only after this call.",
                                "context": context,
                                "initial_checks": initial_checks,
                            }
                        )
                        log["prompt_bytes"]["analyst"] = len(prompt.encode())
                        assert self.provider is not None
                        result = await self.provider.produce(
                            "analyst",
                            prompt,
                            AnalystDraft,
                            8192 if self.backend == "single" else 4096,
                            request.run_id,
                            log["usage"],
                        )
                        draft = AnalystDraft.model_validate(result.model_dump())
                    log["draft"] = draft.model_dump()
                    log["checks"] = [
                        run_check(c, history, request.cutoff_tick) for c in draft.checks
                    ]
                    if self.backend == "multi":
                        prompt = encode_prompt(
                            {
                                "task": "Challenger: audit the analyst against the permitted "
                                "reports, shared initial facts, and selected follow-up results. "
                                "Revise unsupported conclusions and overconfidence. "
                                "Return the complete final assessment.",
                                "context": context,
                                "initial_checks": initial_checks,
                                "analyst": draft.model_dump(),
                                "local_checks": [
                                    compact_check_summary(check, records) for check in log["checks"]
                                ],
                            }
                        )
                        log["prompt_bytes"]["challenger"] = len(prompt.encode())
                        assert self.provider is not None
                        result = await self.provider.produce(
                            "challenger",
                            prompt,
                            Assessment,
                            4096,
                            request.run_id,
                            log["usage"],
                        )
                        final = Assessment.model_validate(result.model_dump())
                    else:
                        # Single-call and mock use the same deterministic final validator.
                        # Mock exercises the full evidence path without paid inference.
                        final = Assessment.model_validate(draft.model_dump(exclude={"checks"}))
                if self.backend == "mock":
                    # Free scripted challenger exercises evidence-based revision.
                    timing = next((c for c in log["checks"] if c["kind"] == "report_timing"), {})
                    if (timing.get("max_delay_ticks") or 0) > 0:
                        final.forecast.probabilities = [
                            0.8 * p + 0.2 / 4 for p in final.forecast.probabilities
                        ]
                        final.hypotheses[0].alternative = (
                            "The report-timing check confirms delayed observations; "
                            "the current distribution may differ from the received sample."
                        )
                if self.backend in {"mock", "multi"}:
                    log["challenger"] = final.model_dump()
                result = validate_assessment(final, request, known_evidence)
                log["assessment"] = result.model_dump()
                log["status"] = "valid"
        except TimeoutError:
            log["status"] = "timeout"
        except asyncio.CancelledError:
            log["status"] = "cancelled"
        except Exception as error:
            log["status"] = "invalid"
            # HTTP errors can include sensitive headers. Log the class, never raw error text.
            log["error"] = type(error).__name__
            log["validation_category"] = (
                "budget"
                if isinstance(error, BudgetExceeded)
                else getattr(error, "category", "schema_or_provider")
            )
        finally:
            log["cycle_budget"] = {
                "ceiling_usd": spend.ceiling_nano / NANO_PER_USD,
                "charged_or_reserved_usd": sum(spend.amounts) / NANO_PER_USD,
            }
            _CURRENT_CYCLE.reset(context_token)
            log["latency_seconds"] = time.monotonic() - started
            log["cost_usd"] = sum(u.get("cost_usd") or 0 for u in log["usage"])
            log["input_tokens"] = sum(u.get("input_tokens", 0) for u in log["usage"])
            log["output_tokens"] = sum(u.get("output_tokens", 0) for u in log["usage"])
        return log
