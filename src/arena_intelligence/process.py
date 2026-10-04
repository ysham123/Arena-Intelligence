"""Async NDJSON subprocess transport, independently draining both output pipes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

MAX_LINE = 1_048_576


class NativeProcess:
    def __init__(self, executable: Path, diagnostics_path: Path | None = None):
        self.executable = Path(executable).resolve()
        self.diagnostics_path = diagnostics_path
        self.process: asyncio.subprocess.Process | None = None
        self.events: asyncio.Queue[dict | None] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.reader_error: Exception | None = None
        self._write_lock = asyncio.Lock()

    async def __aenter__(self) -> NativeProcess:
        # The engine has no need for a cloud API credential.
        child_env = {
            k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
        }
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_LINE,
            env=child_env,
        )
        self.tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]
        return self

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                if len(line) > MAX_LINE:
                    raise ValueError("native line exceeds protocol bound")
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("schema_version") != 1:
                    raise ValueError("invalid native envelope")
                await self.events.put(event)
        except Exception as error:
            self.reader_error = error
        finally:
            await self.events.put(None)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        stream = None
        if self.diagnostics_path:
            self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.diagnostics_path.open("ab")
        try:
            while chunk := await self.process.stderr.read(65536):
                if stream:
                    # Native never receives the key; redact custom-engine diagnostics defensively.
                    key = os.environ.get("ANTHROPIC_API_KEY", "").encode()
                    if key:
                        chunk = chunk.replace(key, b"[REDACTED]")
                    stream.write(chunk)
        finally:
            if stream:
                stream.close()

    async def send(self, payload: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        encoded = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(encoded) > MAX_LINE:
            raise ValueError("outgoing command exceeds protocol bound")
        async with self._write_lock:
            self.process.stdin.write(encoded)
            await self.process.stdin.drain()

    async def receive(self, timeout: float = 30) -> dict:
        event = await asyncio.wait_for(self.events.get(), timeout)
        if event is None:
            if self.reader_error:
                raise self.reader_error
            raise EOFError("native process closed stdout")
        return event

    async def __aexit__(self, *args: Any) -> None:
        assert self.process is not None
        if self.process.returncode is None and self.process.stdin:
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def native_command(executable: Path, *arguments: str) -> dict:
    """Replay/benchmark communicate drains both pipes, with no model or credentials."""
    child_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    process = await asyncio.create_subprocess_exec(
        str(Path(executable).resolve()),
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    try:
        async with asyncio.timeout(120):
            stdout, _stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    if process.returncode != 0:
        raise RuntimeError(f"native command failed with exit code {process.returncode}")
    return json.loads(stdout)
