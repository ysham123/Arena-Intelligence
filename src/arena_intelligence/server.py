"""Local engineering workbench. All integration telemetry is synthetic simulation data."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from .product_adapter import batches, recording_events
from .product_models import ConnectionCreate, ConnectionUpdate, DemoRequest, TelemetryBatch
from .product_store import LIMITS, ProductError, ProductStore, canonical

VERSION = "0.2.0"
ASSETS = Path(__file__).parent / "assets"
ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def resolve_engine(explicit: Path | None = None):
    candidates = (
        [explicit]
        if explicit
        else [Path.cwd() / "build/release/arena-sim", ASSETS / "bin/arena-sim"]
    )
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def safe_file(root: Path, relative: str):
    root = root.resolve()
    original = root / relative
    path = original.resolve()
    if not path.is_relative_to(root) or any(
        item.is_symlink()
        for item in [original, *original.parents]
        if item != root and item.is_relative_to(root)
    ):
        raise ProductError("not_found", "Asset not found", 404)
    if not path.is_file():
        raise ProductError("not_found", "Asset not found", 404)
    return path


class ProductApplication:
    def __init__(
        self,
        data_dir: Path,
        *,
        engine: Path | None = None,
        sample_dir: Path | None = None,
        ledger: Path | None = None,
        assets: Path = ASSETS,
    ):
        self.store = ProductStore(data_dir)
        self.assets = Path(assets)
        self.engine = resolve_engine(engine)
        self.sample_dir = sample_dir.resolve() if sample_dir else None
        self.ledger = (ledger or Path.cwd() / ".arena/costs.sqlite3").resolve()
        self.url = "http://127.0.0.1:8767"
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.processes = {}
        self.process_lock = threading.Lock()
        self.stopping = threading.Event()
        self.insight_cache = {}
        self.references = []
        for path in sorted((self.assets / "reference-pair").glob("*.json")):
            if len(self.references) >= 10:
                break
            self.references.append(path)
        if (self.assets / "sample.json").is_file():
            self.references.append(self.assets / "sample.json")
        for path in self.references:
            with self.store.connect() as db:
                already = db.execute(
                    "SELECT 1 FROM sessions WHERE external_id=?", ("reference:" + path.stem,)
                ).fetchone()
            if not already:
                self.import_asset(path, external_id="reference:" + path.stem)

    def capabilities(self):
        return {
            "native_demo": self.engine is not None,
            "recorded_sample": bool(self.references or self.sample_dir),
            "ingestion": True,
            "paid_native_available": bool(self.engine and os.environ.get("ANTHROPIC_API_KEY")),
        }

    def health(self):
        return {
            "ok": True,
            "version": VERSION,
            "scope": "synthetic_simulation",
            "capabilities": self.capabilities(),
            "limits": LIMITS,
        }

    def summary(self, session_id):
        session = self.store.session(session_id)
        with self.store.connect() as db:
            types = {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT type,COUNT(*) FROM events WHERE session_id=? GROUP BY type",
                    (session_id,),
                )
            }
        return {
            "event_count": session["event_count"],
            "event_types": types,
            "last_source_time": session["last_source_time"],
            "method": "Imported telemetry and deterministic local checks; "
            "no external control is performed.",
        }

    def replay(self, session_id, view=None):
        session = self.store.session(session_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY sequence", (session_id,)
            ).fetchall()
        events = [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "session_id",
                        "sequence",
                        "source_time",
                        "received_at",
                        "type",
                    )
                },
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]
        available = sorted(
            {
                event["payload"].get("visibility", "reported")
                for event in events
                if event["type"] == "snapshot"
            }
        )
        selected = view or ("observer" if "observer" in available else "reported")
        if selected not in {"observer", "evaluator", "reported"}:
            raise ProductError("invalid_view", "Unknown replay view")
        source_frames = [
            event
            for event in events
            if event["type"] == "snapshot"
            and event["payload"].get("visibility", "reported") == selected
        ]
        frames = source_frames
        if len(frames) > 2000:
            frames = [frames[index * (len(frames) - 1) // 1999] for index in range(2000)]
        evidence = [event for event in events if event["type"] == "evidence"]
        metadata = self.store.replay_metadata(session_id)
        return {
            **metadata,
            "session": session,
            "view": selected,
            "available_views": available,
            "frames": [
                {
                    "sequence": event["sequence"],
                    "source_time": event["source_time"],
                    **event["payload"],
                }
                for event in frames
            ],
            "evidence": evidence,
            "evidence_index": {
                record["id"]: event["id"]
                for event in evidence
                for record in event["payload"]["records"]
            },
            "assessments": [event for event in events if event["type"] == "assessment"],
            "summary": metadata.get("summary", self.summary(session_id)),
            "coverage": {
                "retained_events": len(events),
                "source_frames": len(source_frames),
                "returned_frames": len(frames),
                "frames_sampled": len(frames) != len(source_frames),
            },
        }

    def detail(self, session_id):
        session = self.store.session(session_id)
        events = self.store.events(session_id, limit=200, tail=True)
        events = [
            event
            for event in events
            if event["type"] != "snapshot" or event["payload"].get("visibility") != "evaluator"
        ]
        from .product_insights import analyze_session

        key = (session["last_sequence"], session["status"])
        cached = self.insight_cache.get(session_id)
        if cached and cached[0] == key:
            insights = cached[1]
        else:
            insights = analyze_session(events, latest_source_time=session["last_source_time"])
            self.insight_cache[session_id] = (key, insights)
        replay = self.store.replay_metadata(session_id)
        summary = replay.get("summary", self.summary(session_id))
        latest = next((event for event in reversed(events) if event["type"] == "snapshot"), None)
        if latest and "scores" in latest["payload"] and "scores" not in summary:
            summary = {**summary, "scores": latest["payload"]["scores"]}
        return {
            "session": session,
            "events": events,
            "insights": insights,
            "summary": summary,
            "replay": replay,
            "replay_url": f"/api/sessions/{session_id}/replay",
        }

    def review_detail(self, session_id):
        result = self.detail(session_id)
        result.pop("events", None)
        return result

    def overview(self):
        sessions = self.store.sessions()
        return {
            "connections_count": len(self.store.connections()),
            "sessions_count": len(sessions),
            "events_count": sum(session["event_count"] for session in sessions),
            "active_sessions_count": sum(session["status"] == "running" for session in sessions),
            "recent_sessions": sessions[:8],
            "capabilities": self.capabilities(),
        }

    def import_asset(self, path, *, external_id=None, name=None):
        path = safe_file(self.assets, str(path.relative_to(self.assets)))
        if path.stat().st_size > 12 * 1024 * 1024:
            raise ProductError("invalid_sample", "Packaged reference exceeds size limit", 500)
        data = json.loads(path.read_text())
        connection, _ = self.store.create_connection(
            ConnectionCreate(
                name="Recorded reference",
                description="Bundled synthetic simulation recording. No new model calls.",
            )
        )
        external_id = external_id or uuid.uuid4().hex
        session = self.store.create_session(
            connection["id"],
            external_id,
            name or data["name"],
            data.get("source_kind", "recorded_reference"),
        )
        for batch in batches(data["events"], external_id, session["name"]):
            self.store.ingest(
                connection["id"], batch, rate_limit=False, source_kind=session["source_kind"]
            )
        self.store.set_replay(session["id"], data.get("replay", {}))
        return self.store.session(session["id"])

    def native_metadata(self, session_id, directory):
        from .reporting import summarize_run

        manifest = json.loads((directory / "manifest.json").read_text())
        config = manifest["config"]
        summary = summarize_run(directory)
        commands = []
        if (directory / "commands.ndjson").is_file():
            commands = [
                json.loads(line)
                for line in (directory / "commands.ndjson").read_text().splitlines()
            ]
        normalized = {
            key: value for key, value in config.items() if key not in {"paced", "backend"}
        }
        self.store.set_replay(
            session_id,
            {
                "config": config,
                "commands": commands,
                "forecasts": summary.get("forecasts", []),
                "failures": [
                    item for item in summary.get("forecasts", []) if item.get("status") != "scored"
                ],
                "summary": summary,
                "comparison_context": {
                    "scenario_fingerprint": hashlib.sha256(
                        canonical(normalized).encode()
                    ).hexdigest(),
                    "scenario_label": (
                        f"Seed {config['seed']} · "
                        f"{config['bots_per_team']} bots/team · "
                        f"{config['duration_ticks'] / 10:g}s"
                    ),
                },
            },
        )

    def new_demo(self, data: DemoRequest):
        if data.mode == "recorded":
            if self.references:
                return self.import_asset(self.references[0], name=data.name)
            if self.sample_dir:
                return self.import_recording(self.sample_dir, data.name)
            raise ProductError("unavailable", "No bundled recording is available", 503)
        if not self.engine:
            raise ProductError(
                "engine_unavailable", "Build or install the native simulation engine first", 503
            )
        paid = data.backend in {"single", "multi"}
        if paid and not data.allow_paid:
            raise ProductError(
                "paid_confirmation_required", "Paid native reasoning requires allow_paid=true", 403
            )
        if paid and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProductError(
                "credentials_unavailable",
                "Configure an API credential on the local server before selecting paid reasoning",
                409,
            )
        with self.job_lock:
            if self.stopping.is_set():
                raise ProductError("stopping", "Local runtime is stopping", 503)
            if sum(thread.is_alive() for thread in self.jobs.values()) >= 2:
                raise ProductError("busy", "Two local demo runs are already active", 429)
            connection, _ = self.store.create_connection(
                ConnectionCreate(
                    name="Native arena",
                    description="Local synthetic simulation through the existing arena pipeline.",
                )
            )
            external_id = uuid.uuid4().hex
            directory = self.store.directory / "runs" / external_id
            session = self.store.create_session(
                connection["id"], external_id, data.name, "native_" + data.backend, directory
            )
            thread = threading.Thread(
                target=self.native_job,
                args=(session, connection["id"], directory, data),
                daemon=True,
            )
            self.jobs[session["id"]] = thread
            thread.start()
        return session

    def import_recording(self, directory, name):
        connection, _ = self.store.create_connection(ConnectionCreate(name="Local recorded sample"))
        external_id = uuid.uuid4().hex
        session = self.store.create_session(
            connection["id"], external_id, name, "recorded_native", directory
        )
        for batch in batches(recording_events(directory), external_id, name):
            self.store.ingest(connection["id"], batch, rate_limit=False)
        self.native_metadata(session["id"], directory)
        return self.store.session(session["id"])

    def native_job(self, session, connection_id, directory, data):
        command = [
            sys.executable,
            "-m",
            "arena_intelligence",
            "run",
            "--engine",
            str(self.engine),
            "--backend",
            data.backend,
            "--run-dir",
            str(directory),
            "--duration-ticks",
            "900",
            "--planning-ticks",
            "0,300,600",
            "--opponent",
            "switch",
            "--paced",
            "--no-viewer",
        ]
        paid = data.backend in {"single", "multi"}
        if paid:
            command.extend(
                ["--allow-paid", "--budget-bucket", "demo", "--ledger", str(self.ledger)]
            )
        env = dict(os.environ)
        if not paid:
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("OPENAI_API_KEY", None)
        process = None
        seen = set()
        sequence = 0

        def capture(final=False):
            nonlocal sequence
            try:
                events = recording_events(directory, live=not final)
            except (OSError, ValueError, KeyError):
                if final:
                    raise
                return
            fresh = []
            for event in events:
                marker = hashlib.sha256(
                    canonical(
                        {key: value for key, value in event.items() if key != "sequence"}
                    ).encode()
                ).hexdigest()
                if marker in seen:
                    continue
                seen.add(marker)
                fresh.append({**event, "sequence": sequence})
                sequence += 1
            for batch in batches(fresh, session["external_id"], session["name"], completed=False):
                self.store.ingest(connection_id, batch, rate_limit=False, allow_late=True)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=os.name == "posix",
            )
            with self.process_lock:
                self.processes[session["id"]] = process
            deadline = time.monotonic() + 150
            while process.poll() is None:
                if self.stopping.is_set():
                    self.stop_process(process)
                    raise RuntimeError("local runtime stopped")
                if time.monotonic() > deadline:
                    self.stop_process(process)
                    raise TimeoutError("native demo timeout")
                capture()
                time.sleep(0.5)
            process.communicate(timeout=5)
            if process.returncode != 0:
                raise RuntimeError("native demo failed")
            capture(final=True)
            self.native_metadata(session["id"], directory)
            self.store.set_session_status(session["id"], "completed")
        except Exception as error:
            if process and process.poll() is None:
                self.stop_process(process)
            try:
                current = self.store.session(session["id"])
                message = (
                    "Local runtime stopped"
                    if self.stopping.is_set()
                    else "Native worker failed: " + type(error).__name__
                )
                failure = {
                    "schema_version": 1,
                    "scope": "synthetic_simulation",
                    "session": {
                        "external_id": session["external_id"],
                        "name": session["name"],
                        "status": "stopped",
                    },
                    "events": [
                        {
                            "sequence": current["last_sequence"] + 1,
                            "source_time": current["last_source_time"],
                            "type": "lifecycle",
                            "payload": {"status": "failed", "message": message},
                        }
                    ],
                }
                self.store.ingest(
                    connection_id, TelemetryBatch.model_validate(failure), rate_limit=False
                )
            except Exception:
                pass
            self.store.set_session_status(
                session["id"], "stopped" if self.stopping.is_set() else "failed"
            )
        finally:
            with self.process_lock:
                self.processes.pop(session["id"], None)

    @staticmethod
    def stop_process(process):
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait(timeout=3)

    def shutdown(self):
        self.stopping.set()
        with self.process_lock:
            processes = list(self.processes.values())
        for process in processes:
            self.stop_process(process)
        with self.job_lock:
            threads = list(self.jobs.values())
        for thread in threads:
            thread.join(timeout=5)


class ProductHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, ProductHandler)
        host, port = self.server_address[:2]
        app.url = f"http://{host}:{port}"

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class ProductHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass

    @property
    def app(self):
        return self.server.app

    def boundary(self, mutation=False):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise ProductError("invalid_host", "Local host header required", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            raise ProductError("foreign_origin", "Foreign browser origins are not allowed", 403)
        if mutation and self.headers.get("Transfer-Encoding"):
            raise ProductError("invalid_body", "Chunked bodies are not supported", 400)

    def body(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ProductError("length_required", "Content-Length is required", 411)
        try:
            length = int(raw)
        except ValueError:
            raise ProductError("invalid_body", "Invalid Content-Length") from None
        if not 0 <= length <= LIMITS["body_bytes"]:
            raise ProductError("body_too_large", "Request body exceeds 256 KiB", 413)
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ProductError("content_type", "Content-Type must be application/json", 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ProductError("invalid_body", "Incomplete request body")
        try:
            value = json.loads(
                raw or b"{}", parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
            )
        except (ValueError, UnicodeError):
            raise ProductError("invalid_json", "Request body must be finite JSON") from None
        if not isinstance(value, dict):
            raise ProductError("invalid_json", "Request body must be a JSON object")
        return value

    def respond(
        self, status, value, *, content_type="application/json", attachment=None, script_hashes=()
    ):
        body = (
            json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
            if content_type == "application/json"
            else value
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' "
            + " ".join(script_hashes)
            + "; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Connection", "close")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def route(self, method):
        self.boundary(method != "GET")
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        store = self.app.store
        if method == "GET" and path == "/api/health":
            return 200, self.app.health()
        if method == "GET" and path == "/api/settings":
            return 200, {
                "bind_host": "127.0.0.1",
                "port": self.server.server_address[1],
                "data_directory": str(store.directory),
                **self.app.health(),
            }
        if method == "GET" and path == "/api/overview":
            return 200, self.app.overview()
        if path == "/api/connections":
            if method == "GET":
                return 200, {"connections": store.connections()}
            if method == "POST":
                connection, token = store.create_connection(
                    ConnectionCreate.model_validate(self.body())
                )
                return 201, {
                    "connection": connection,
                    "integration_token": token,
                    "ingest_url": self.app.url + "/api/ingest",
                }
        match = re.fullmatch(r"/api/connections/([a-f0-9]{32})(/test)?", path)
        if match:
            connection_id, action = match.groups()
            if method == "POST" and action:
                self.body()
                connection = store.connection(connection_id)
                return 200, {
                    "ok": True,
                    "status": connection["status"],
                    "last_seen_at": connection["last_seen_at"],
                    "message": "Recent telemetry has been received"
                    if connection["last_seen_at"]
                    else "Integration identity is ready; awaiting authenticated telemetry",
                }
            if method == "PATCH" and not action:
                return 200, {
                    "connection": store.update_connection(
                        connection_id, ConnectionUpdate.model_validate(self.body())
                    )
                }
            if method == "DELETE" and not action:
                self.body()
                return 200, store.delete_connection(connection_id)
        if method == "POST" and path == "/api/ingest":
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ProductError(
                    "unauthorized", "A valid integration bearer token is required", 401
                )
            connection_id = store.authorize(authorization[7:])
            result = store.ingest(connection_id, TelemetryBatch.model_validate(self.body()))
            return 200, result
        if method == "GET" and path == "/api/sessions":
            return 200, {"sessions": store.sessions()}
        if method == "POST" and path == "/api/demo":
            return 202, {"session": self.app.new_demo(DemoRequest.model_validate(self.body()))}
        if method == "GET" and path == "/api/review":
            from .product_review import build_review

            return 200, build_review([self.app.review_detail(s["id"]) for s in store.sessions()])
        if method == "GET" and path == "/api/compare":
            from .product_review import compare_sessions

            left = query.get("left", [""])[0]
            right = query.get("right", [""])[0]
            return 200, compare_sessions(
                self.app.review_detail(left), self.app.review_detail(right)
            )
        match = re.fullmatch(r"/api/sessions/([a-f0-9]{32})(/(events|replay|viewer|export))?", path)
        if method == "GET" and match:
            session_id, _, action = match.groups()
            if action == "events":
                try:
                    after = int(query.get("after", ["-1"])[0])
                    limit = int(query.get("limit", ["200"])[0])
                except ValueError:
                    raise ProductError(
                        "invalid_cursor", "Event cursor and limit must be integers"
                    ) from None
                if after < -1 or not 1 <= limit <= 500:
                    raise ProductError("invalid_cursor", "Event limit must be 1..500")
                raw_events = store.events(session_id, after=after, limit=limit)
                view = query.get("view", ["observer"])[0]
                if view not in {"observer", "reported", "evaluator"}:
                    raise ProductError("invalid_view", "Unknown event view")
                events = [
                    event
                    for event in raw_events
                    if event["type"] != "snapshot"
                    or event["payload"].get("visibility", "reported") != "evaluator"
                    or view == "evaluator"
                ]
                return 200, {
                    "events": events,
                    "next_after": raw_events[-1]["sequence"] if raw_events else after,
                    "session": store.session(session_id),
                    "view": view,
                }
            if action == "replay":
                return 200, self.app.replay(session_id, query.get("view", [None])[0])
            if action == "export":
                self.respond(
                    200, self.app.detail(session_id), attachment=f"arena-session-{session_id}.json"
                )
                return None
            if action == "viewer":
                run = store.run_path(session_id)
                if not run:
                    raise ProductError(
                        "not_available",
                        "This session uses the app workspace; no native viewer is attached",
                        404,
                    )
                directory = Path(run).resolve()
                if (
                    not directory.is_relative_to(store.directory / "runs")
                    and directory != self.app.sample_dir
                ):
                    raise ProductError("not_available", "Native recording is not available", 404)
                from .viewer import generate_viewer

                viewer = generate_viewer(directory)
                html = viewer.read_bytes()
                hashes = [
                    "'sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode() + "'"
                    for script in re.findall(
                        rb"<script\b[^>]*>(.*?)</script\s*>", html, re.I | re.S
                    )
                ]
                self.respond(
                    200, html, content_type="text/html; charset=utf-8", script_hashes=hashes
                )
                return None
            return 200, self.app.detail(session_id)
        if method == "GET" and not path.startswith("/api/"):
            if path.startswith("/fonts/"):
                asset = safe_file(self.app.assets / "fonts", path[7:])
            else:
                relative = (
                    path.removeprefix("/app/") if path.startswith("/app/") else path.lstrip("/")
                )
                if not relative or "." not in relative.split("/")[-1]:
                    relative = "index.html"
                asset = safe_file(self.app.assets / "app", relative)
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self.respond(200, asset.read_bytes(), content_type=content_type)
            return None
        raise ProductError("not_found", "Endpoint not found", 404)

    def dispatch(self, method):
        try:
            result = self.route(method)
            if result:
                self.respond(*result)
        except ProductError as error:
            self.respond(error.status, {"error": {"code": error.code, "message": error.message}})
        except ValidationError:
            self.respond(
                400,
                {
                    "error": {
                        "code": "invalid_schema",
                        "message": "Request does not match the synthetic simulation contract",
                    }
                },
            )
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.respond(
                500,
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Local operation failed; no credential details are exposed",
                    }
                },
            )

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_OPTIONS(self):
        self.respond(
            403, {"error": {"code": "cors_disabled", "message": "Use the local app origin"}}
        )


def make_server(
    host="127.0.0.1",
    port=8767,
    *,
    data_dir=Path(".arena/app"),
    engine=None,
    sample_dir=None,
    ledger=None,
    assets=ASSETS,
):
    if host == "localhost":
        host = "127.0.0.1"
    try:
        allowed = ipaddress.ip_address(host).is_loopback and host == "127.0.0.1"
    except ValueError:
        allowed = False
    if not allowed or not 0 <= port <= 65535:
        raise ValueError("Product server must bind to 127.0.0.1 with a valid local port")
    return ProductHTTPServer(
        (host, port),
        ProductApplication(
            data_dir, engine=engine, sample_dir=sample_dir, ledger=ledger, assets=assets
        ),
    )


def serve(host="127.0.0.1", port=8767, **kwargs):
    server = make_server(host, port, **kwargs)
    print(
        json.dumps(
            {"app_url": server.app.url, "version": VERSION, "scope": "synthetic_simulation"}
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.app.shutdown()
        server.server_close()
