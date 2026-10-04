"""Local product persistence, honest boundaries and HTTP security; no model calls."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arena_intelligence.product_models import ConnectionCreate, DemoRequest, TelemetryBatch
from arena_intelligence.product_store import LIMITS, ProductError, ProductStore
from arena_intelligence.server import ProductApplication, make_server


def batch(sequence=1, *, events=None, status="running", external_id="sim-1"):
    return TelemetryBatch.model_validate(
        {
            "schema_version": 1,
            "scope": "synthetic_simulation",
            "session": {"external_id": external_id, "name": "Test simulation", "status": status},
            "events": events
            or [
                {
                    "sequence": sequence,
                    "source_time": float(sequence),
                    "type": "snapshot",
                    "payload": {
                        "entities": [{"id": "a", "x": 1.0, "y": 2.0}],
                        "world": {"width": 32.0, "height": 32.0},
                    },
                }
            ],
        }
    )


def connection(store):
    return store.create_connection(ConnectionCreate(name="My simulator"))


def test_token_hashing_persistence_idempotency_and_revocation(tmp_path):
    store = ProductStore(tmp_path)
    identity, token = connection(store)
    assert token.encode() not in store.path.read_bytes()
    assert store.authorize(token) == identity["id"]
    first = store.ingest(identity["id"], batch())
    assert first["accepted_events"] == 1
    duplicate = store.ingest(identity["id"], batch())
    assert duplicate["duplicate_events"] == 1 and duplicate["session"]["event_count"] == 1
    restarted = ProductStore(tmp_path)
    assert restarted.events(first["session"]["id"])[0]["payload"]["entities"][0]["x"] == 1
    assert token not in json.dumps(restarted.connections())
    restarted.delete_connection(identity["id"])
    with pytest.raises(ProductError, match="valid integration"):
        restarted.authorize(token)
    assert restarted.session(first["session"]["id"])["event_count"] == 1


def test_closed_session_retry_cannot_reopen_or_replace_an_event(tmp_path):
    store = ProductStore(tmp_path)
    identity, _ = connection(store)
    result = store.ingest(identity["id"], batch(status="completed"))
    retried = store.ingest(identity["id"], batch(status="running"))
    assert retried["session"]["status"] == "completed"
    with pytest.raises(ProductError, match="new external"):
        store.ingest(identity["id"], batch(2))
    altered = batch()
    altered.events[0].payload["entities"][0]["x"] = 3.0
    with pytest.raises(ProductError, match="reused"):
        store.ingest(identity["id"], altered)
    assert store.session(result["session"]["id"])["event_count"] == 1


def test_storage_quota_rejects_atomically_and_preserves_accepted_history(tmp_path, monkeypatch):
    store = ProductStore(tmp_path)
    identity, _ = connection(store)
    first = store.ingest(identity["id"], batch())
    monkeypatch.setitem(LIMITS, "events_per_session", 1)
    with pytest.raises(ProductError, match="preserved") as error:
        store.ingest(identity["id"], batch(2))
    assert error.value.status == 409
    assert store.session(first["session"]["id"])["last_sequence"] == 1
    assert len(store.events(first["session"]["id"])) == 1


def test_received_evidence_references_block_unknown_and_future_records(tmp_path):
    store = ProductStore(tmp_path)
    identity, _ = connection(store)
    assessment = {
        "sequence": 1,
        "source_time": 1.0,
        "type": "assessment",
        "payload": {
            "status": "valid",
            "origin": "model",
            "hypotheses": [
                {
                    "claim": "Observed pattern",
                    "evidence_ids": ["e1"],
                    "alternative": "Another explanation",
                }
            ],
        },
    }
    with pytest.raises(ProductError) as rejected:
        store.ingest(identity["id"], batch(events=[assessment]))
    assert rejected.value.code == "unknown_evidence"
    assert store.sessions() == []
    evidence = {
        "sequence": 2,
        "source_time": 2.0,
        "type": "evidence",
        "payload": {
            "records": [
                {
                    "id": "e1",
                    "kind": "sighting",
                    "observed_at": 1.0,
                    "received_at": 2.0,
                    "summary": "Received synthetic sighting",
                }
            ]
        },
    }
    with pytest.raises(ProductError):
        store.ingest(identity["id"], batch(events=[assessment, evidence]))
    evidence["sequence"] = 0
    evidence["source_time"] = 1.0
    evidence["payload"]["records"][0]["received_at"] = 1.0
    accepted = store.ingest(identity["id"], batch(events=[evidence, assessment]))
    assert accepted["accepted_events"] == 2
    assert (
        store.events(accepted["session"]["id"])[1]["payload"]["validation_scope"]
        == "schema_and_received_evidence_references_only"
    )


def test_connection_health_becomes_stale(tmp_path):
    store = ProductStore(tmp_path)
    identity, _ = connection(store)
    store.ingest(identity["id"], batch())
    with store.connect() as db:
        db.execute(
            "UPDATE connections SET last_seen_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), identity["id"]),
        )
    assert store.connection(identity["id"])["status"] == "stale"


@pytest.fixture
def local_server(tmp_path):
    assets = tmp_path / "assets"
    (assets / "app").mkdir(parents=True)
    (assets / "app/index.html").write_text("<html>Local app</html>")
    server = make_server(
        port=0, data_dir=tmp_path / "data", engine=tmp_path / "missing-engine", assets=assets
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.app.shutdown()
    server.server_close()
    worker.join(timeout=5)


def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    raw = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", **(headers or {})}
    client.request(method, path, body=raw, headers=headers)
    response = client.getresponse()
    data = response.read()
    status = response.status
    client.close()
    return status, json.loads(data) if data.startswith(b"{") else data


def test_http_onboarding_stream_cursor_and_restart_records(local_server):
    status, result = request(local_server, "POST", "/api/connections", {"name": "Connected sim"})
    assert status == 201 and result["ingest_url"].startswith(local_server.app.url)
    token = result["integration_token"]
    status, accepted = request(
        local_server,
        "POST",
        "/api/ingest",
        batch().model_dump(),
        {"Authorization": "Bearer " + token},
    )
    assert status == 200
    session_id = accepted["session"]["id"]
    status, events = request(local_server, "GET", f"/api/sessions/{session_id}/events?after=0")
    assert status == 200 and events["next_after"] == 1 and len(events["events"]) == 1
    _, detail = request(local_server, "GET", f"/api/sessions/{session_id}")
    assert detail["session"]["scope"] == "synthetic_simulation"
    assert "frames" not in detail["replay"]
    _, export = request(local_server, "GET", f"/api/sessions/{session_id}/export")
    assert export["session"]["id"] == session_id


def test_http_rejects_foreign_origins_rebinding_traversal_and_non_json(local_server):
    for headers in ({"Origin": "https://foreign.example"}, {"Host": "evil.example"}):
        status, _ = request(local_server, "POST", "/api/connections", {"name": "test"}, headers)
        assert status == 403
    assert (
        request(
            local_server,
            "POST",
            "/api/connections",
            {"name": "test"},
            {"Content-Type": "text/plain"},
        )[0]
        == 415
    )
    for path in ("/app/../../product.sqlite3", "/.env", "/api/unknown"):
        assert request(local_server, "GET", path)[0] == 404
    outside = local_server.app.store.directory / "secret.txt"
    outside.write_text("private")
    (local_server.app.assets / "app/leak.txt").symlink_to(outside)
    assert request(local_server, "GET", "/app/leak.txt")[0] == 404


def test_ingestion_needs_auth_synthetic_scope_and_bounded_body(local_server):
    assert request(local_server, "POST", "/api/ingest", batch().model_dump())[0] == 401
    identity, token = connection(local_server.app.store)
    invalid = batch().model_dump()
    invalid["scope"] = "real_operations"
    assert (
        request(local_server, "POST", "/api/ingest", invalid, {"Authorization": "Bearer " + token})[
            0
        ]
        == 400
    )
    assert (
        request(
            local_server,
            "POST",
            "/api/connections",
            {"name": "x"},
            {"Content-Length": str(LIMITS["body_bytes"] + 1)},
        )[0]
        == 413
    )
    assert local_server.app.store.connection(identity["id"])["event_count"] == 0


def test_live_action_without_engine_is_honest_and_no_paid_call_is_implicit(local_server):
    status, result = request(
        local_server, "POST", "/api/demo", {"mode": "native", "backend": "mock"}
    )
    assert status == 503 and result["error"]["code"] == "engine_unavailable"
    local_server.app.engine = Path(sys.executable)
    with pytest.raises(ProductError) as error:
        local_server.app.new_demo(DemoRequest(mode="native", backend="single"))
    assert error.value.code == "paid_confirmation_required"


def test_default_observer_does_not_serve_evaluator_truth(local_server):
    store = local_server.app.store
    identity, _ = connection(store)
    observer = batch().events[0].model_dump()
    observer["payload"]["visibility"] = "observer"
    evaluator = {
        **observer,
        "sequence": 2,
        "payload": {
            **observer["payload"],
            "visibility": "evaluator",
            "entities": [{"id": "hidden", "x": 20.0, "y": 20.0}],
        },
    }
    accepted = store.ingest(identity["id"], batch(events=[observer, evaluator]))
    session_id = accepted["session"]["id"]
    _, stream = request(local_server, "GET", f"/api/sessions/{session_id}/events")
    assert len(stream["events"]) == 1 and stream["next_after"] == 2
    _, replay = request(local_server, "GET", f"/api/sessions/{session_id}/replay")
    assert replay["view"] == "observer" and len(replay["frames"]) == 1
    assert replay["frames"][0]["entities"][0]["id"] == "a"
    _, truth = request(local_server, "GET", f"/api/sessions/{session_id}/replay?view=evaluator")
    assert truth["frames"][0]["entities"][0]["id"] == "hidden"


@pytest.mark.skipif(os.name != "posix", reason="native product supports POSIX process groups")
def test_runtime_shutdown_terminates_tracked_worker_group(tmp_path):
    app = ProductApplication(
        tmp_path / "data", engine=tmp_path / "missing", assets=tmp_path / "assets"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True
    )
    app.processes["worker"] = process
    app.shutdown()
    assert process.poll() is not None and app.stopping.is_set()


def test_public_server_bind_is_rejected_before_creating_data(tmp_path):
    with pytest.raises(ValueError, match="127.0.0.1"):
        make_server("0.0.0.0", 8767, data_dir=tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_legacy_replay_csp_authorizes_only_exact_embedded_scripts(local_server, monkeypatch):
    store = local_server.app.store
    identity, _ = connection(store)
    directory = store.directory / "runs" / "recording"
    directory.mkdir(parents=True)
    script = b"window.replayReady = true;"
    html = b"<html><script>" + script + b"</script></html>"
    viewer = directory / "viewer.html"
    viewer.write_bytes(html)
    session = store.create_session(
        identity["id"], "recording", "Recorded run", "native", run_path=directory
    )
    import arena_intelligence.viewer

    monkeypatch.setattr(arena_intelligence.viewer, "generate_viewer", lambda _: viewer)
    client = http.client.HTTPConnection("127.0.0.1", local_server.server_address[1], timeout=5)
    client.request("GET", f"/api/sessions/{session['id']}/viewer")
    response = client.getresponse()
    policy = response.getheader("Content-Security-Policy")
    assert response.status == 200 and response.read() == html
    expected = base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert "'sha256-" + expected + "'" in policy
    assert "script-src 'self' 'sha256-" in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy
    client.close()
