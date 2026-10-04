"""Paced nonblocking runs, bounded worker failures, and explicit batch advancement."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from .process import NativeProcess
from .protocol import ObservationEnvelope, RunConfig, envelope
from .reasoner import Reasoner, reasoning_config


class NativeProtocolError(RuntimeError):
    """A native error envelope is terminal for this host invocation."""


def append_json(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")


async def run_match(
    executable: Path,
    run_dir: Path,
    config: RunConfig,
    reasoner: Reasoner,
    deadline_seconds: float = 20.0,
) -> dict:
    if not config.paced and reasoner.backend in {"single", "multi"}:
        raise ValueError("live Claude calls are forbidden in advance-based batch mode")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / "manifest.json").exists() or (run_dir / "reasoning.ndjson").exists():
        raise ValueError("run directory already contains records; choose a fresh directory")
    run_id = uuid.uuid4().hex
    history: deque[ObservationEnvelope] = deque(maxlen=8)
    known_evidence: set[str] = set()
    issued: dict[str, ObservationEnvelope] = {}
    consumed: set[str] = set()
    fallback_sent: set[str] = set()
    latest: ObservationEnvelope | None = None
    active: asyncio.Task | None = None
    started = time.monotonic()
    opportunities = 0
    completed_cycles = 0
    terminal: dict[str, Any] = {}
    nonrecoverable = {
        "invalid_envelope",
        "unknown_request",
        "consumed_request",
        "cutoff_mismatch",
        "run_finished",
        "stale_request",
    }

    async with NativeProcess(executable, run_dir / "native.stderr.log") as native:

        async def receive(timeout: float = 30) -> dict:
            event = await native.receive(timeout)
            if event.get("run_id") != run_id:
                raise ValueError("native returned a different run ID")
            append_json(run_dir / "host_events.ndjson", event)
            if event["type"] == "error":
                raise NativeProtocolError(
                    "native reported a protocol error; inspect host event logs"
                )
            if event["type"] in {"assessment_result", "fallback_result"} and event.get("accepted"):
                consumed.add(event.get("request_id", ""))
            return event

        async def send_fallback(request: ObservationEnvelope, reason: str) -> dict | None:
            if (
                terminal
                or latest is None
                or latest.request_id != request.request_id
                or request.request_id in consumed
                or request.request_id in fallback_sent
            ):
                return None
            command = envelope(
                "fallback",
                run_id,
                request_id=request.request_id,
                cutoff_tick=request.cutoff_tick,
                reason=reason,
            )
            fallback_sent.add(request.request_id)
            try:
                await native.send(command)
            except (BrokenPipeError, ConnectionResetError):
                return None
            return command

        async def wait_for_result(kind: str, request_id: str) -> dict:
            while True:
                reply = await receive(10)
                if reply["type"] == kind:
                    if reply.get("request_id") != request_id:
                        raise ValueError("native result request mismatch")
                    return reply

        async def recover_rejection(reply: dict) -> dict | None:
            request = issued.get(reply.get("request_id", ""))
            if request is None or reply.get("reason") in nonrecoverable:
                return None
            return await send_fallback(request, "native_rejected")

        async def cycle_and_send(
            request: ObservationEnvelope, snapshot: list[ObservationEnvelope], known: set[str]
        ) -> dict:
            nonlocal completed_cycles
            worker_started = time.monotonic()
            try:
                log = await reasoner.cycle(request, snapshot, known, deadline_seconds)
            except asyncio.CancelledError:
                log = {
                    "type": "reasoning_cycle",
                    "backend": reasoner.backend,
                    "mode": reasoner.backend,
                    "request_id": request.request_id,
                    "cutoff_tick": request.cutoff_tick,
                    "status": "cancelled",
                    "assessment": None,
                    "usage": [],
                    "latency_seconds": time.monotonic() - worker_started,
                }
            except Exception as error:
                # Worker failures must not leak exception text or leave old advice active.
                log = {
                    "type": "reasoning_cycle",
                    "backend": reasoner.backend,
                    "mode": reasoner.backend,
                    "request_id": request.request_id,
                    "cutoff_tick": request.cutoff_tick,
                    "status": "worker_error",
                    "error": type(error).__name__,
                    "validation_category": "host_error",
                    "assessment": None,
                    "usage": [],
                    "latency_seconds": time.monotonic() - worker_started,
                }
            log.setdefault("model_config", reasoning_config(reasoner.backend))
            log.setdefault("cycle_budget", {"ceiling_usd": 5, "charged_or_reserved_usd": 0})
            log["command_sent"] = None
            if log.get("assessment") is not None:
                if terminal or latest is None or latest.request_id != request.request_id:
                    log["discarded_assessment"] = log["assessment"]
                    log["assessment"] = None
                    log["status"] = "superseded"
                else:
                    try:
                        await native.send(log["assessment"])
                        log["command_sent"] = "assessment"
                    except (BrokenPipeError, ConnectionResetError):
                        log["status"] = "native_closed"
            if log.get("assessment") is None:
                reason = "budget" if log.get("validation_category") == "budget" else log["status"]
                fallback = await send_fallback(request, reason)
                if fallback:
                    log["fallback"] = fallback
                    log["command_sent"] = "fallback"
            append_json(run_dir / "reasoning.ndjson", log)
            completed_cycles += 1
            return log

        try:
            await native.send(
                envelope("start", run_id, config=config.model_dump(), record_dir=str(run_dir))
            )
            while True:
                event = await receive(max(30, config.duration_ticks / config.tick_hz + 30))
                kind = event["type"]
                if kind in {"finished", "stopped"}:
                    terminal = event
                    break
                if kind == "assessment_result" and not event.get("accepted"):
                    await recover_rejection(event)
                    continue
                if kind != "observation":
                    continue
                request = ObservationEnvelope.model_validate(event)
                if request.request_id in issued:
                    raise ValueError("duplicate native request ID")
                issued[request.request_id] = request
                latest = request
                opportunities += 1
                history.append(request)
                known_evidence.update(e.id for e in request.observation.evidence)
                known_evidence.update(
                    r.evidence_id
                    for r in request.observation.last_seen + request.observation.visible_opponents
                )
                snapshot, known = list(history), set(known_evidence)
                if config.paced:
                    if active is not None and not active.done():
                        fallback = await send_fallback(request, "busy")
                        append_json(
                            run_dir / "reasoning.ndjson",
                            {
                                "type": "reasoning_cycle",
                                "backend": reasoner.backend,
                                "mode": reasoner.backend,
                                "model_config": reasoning_config(reasoner.backend),
                                "cycle_budget": {"ceiling_usd": 5, "charged_or_reserved_usd": 0},
                                "request_id": request.request_id,
                                "cutoff_tick": request.cutoff_tick,
                                "status": "busy",
                                "assessment": None,
                                "fallback": fallback,
                                "usage": [],
                                "latency_seconds": 0,
                            },
                        )
                        continue
                    if active is not None:
                        await active
                    active = asyncio.create_task(cycle_and_send(request, snapshot, known))
                else:
                    log = await cycle_and_send(request, snapshot, known)
                    if log["command_sent"]:
                        expected = (
                            "assessment_result"
                            if log["command_sent"] == "assessment"
                            else "fallback_result"
                        )
                        reply = await wait_for_result(expected, request.request_id)
                        if expected == "assessment_result" and not reply.get("accepted"):
                            if await recover_rejection(reply):
                                await wait_for_result("fallback_result", request.request_id)
                    next_tick = next(
                        (t for t in config.planning_ticks if t > request.cutoff_tick),
                        config.duration_ticks,
                    )
                    await native.send(
                        envelope("advance", run_id, ticks=next_tick - request.cutoff_tick)
                    )
        finally:
            if active is not None and not active.done():
                active.cancel()
            if active is not None:
                await asyncio.gather(active, return_exceptions=True)
            if native.process is not None and native.process.returncode is None:
                try:
                    await native.send(envelope("stop", run_id))
                except (BrokenPipeError, ConnectionResetError):
                    pass
    summary = {
        "run_id": run_id,
        "backend": reasoner.backend,
        "mode": "paced" if config.paced else "batch",
        "planning_opportunities": opportunities,
        "completed_cycles": completed_cycles,
        "duration_seconds": time.monotonic() - started,
        "terminal": terminal,
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
