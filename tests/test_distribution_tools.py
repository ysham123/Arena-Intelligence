"""Install/adapter checks use isolated paths and local fixture servers; zero model calls."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


sdk = module("arena_adapter")
installer = module("install_local")
sample = module("simulation_adapter")


@pytest.fixture
def endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(content)
            calls.append((self.path, self.headers.get("Authorization"), payload))
            if self.path == "/api/connections":
                self.send(
                    201,
                    {"connection": {"id": "local-source"}, "integration_token": "fixture-token"},
                )
            elif self.headers.get("Authorization") != "Bearer fixture-token":
                self.send(401, {"detail": "invalid token"})
            else:
                self.send(
                    200,
                    {
                        "session": {"id": "local-session"},
                        "accepted_events": len(payload["events"]),
                        "duplicate_events": 0,
                    },
                )

        def send(self, status, payload):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", calls=calls)
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_sdk_creates_connection_and_publishes_exact_synthetic_batch(endpoint):
    client = sdk.ArenaAdapter(endpoint.url)
    assert client.connect("Fixture")["id"] == "local-source"
    batch = sample.synthetic_batch(0, source_time=0, sequence=1, final=True)
    original = json.dumps(batch, sort_keys=True)
    result = client.publish("external-fixture", "Synthetic fixture", batch, status="completed")
    assert result["accepted_events"] == len(batch)
    assert json.dumps(batch, sort_keys=True) == original
    assert endpoint.calls[-1][1] == "Bearer fixture-token"
    payload = endpoint.calls[-1][2]
    assert payload["scope"] == "synthetic_simulation"
    assert payload["session"]["status"] == "completed"
    assert {e["type"] for e in payload["events"]} == {
        "snapshot",
        "evidence",
        "assessment",
        "metric",
        "lifecycle",
    }
    assessment = next(e["payload"] for e in batch if e["type"] == "assessment")
    assert assessment["status"] == assessment["origin"] == "scripted"


def test_sample_streams_monotonic_sequences_and_hides_token(endpoint, monkeypatch, capsys):
    monkeypatch.delenv("ARENA_INTEGRATION_TOKEN", raising=False)
    monkeypatch.setattr(sample.time, "sleep", lambda duration: None)
    sample.main(["--url", endpoint.url, "--steps", "3", "--session-id", "sample-fixture"])
    assert len(endpoint.calls) == 4
    batches = [call[2] for call in endpoint.calls if call[0] == "/api/ingest"]
    sequences = [e["sequence"] for batch in batches for e in batch["events"]]
    assert sequences == list(range(1, len(sequences) + 1))
    assert [batch["session"]["status"] for batch in batches] == ["running", "running", "completed"]
    assert all(
        e["source_time"] == index for index, batch in enumerate(batches) for e in batch["events"]
    )
    assert "fixture-token" not in capsys.readouterr().out


def test_sdk_rejects_remote_origins_invalid_values_and_oversized_batches(endpoint):
    for value in (
        "https://example.com",
        "http://localhost.evil",
        "http://u:p@localhost",
        "http://localhost/api",
        "http://localhost/?token=x",
    ):
        with pytest.raises(ValueError, match="loopback"):
            sdk.ArenaAdapter(value)
    with pytest.raises(ValueError, match="ASCII"):
        sdk.ArenaAdapter(endpoint.url, token="secret\nvalue")
    with pytest.raises(ValueError, match="finite"):
        sdk.ArenaAdapter(endpoint.url, timeout=float("inf"))
    client = sdk.ArenaAdapter(endpoint.url, token="fixture-token")
    with pytest.raises(ValueError, match="100"):
        client.publish("r", "r", [])
    with pytest.raises(ValueError, match="256 KiB"):
        client.publish("r", "r", [{"data": "x" * sdk.MAX_BODY_BYTES}])
    with pytest.raises(ValueError):
        client.publish("r", "r", [{"data": float("nan")}])
    assert not endpoint.calls


def test_authentication_errors_do_not_expose_token(endpoint):
    client = sdk.ArenaAdapter(endpoint.url, token="sensitive-fixture")
    with pytest.raises(sdk.AdapterError, match="HTTP 401") as error:
        client.publish(
            "r",
            "r",
            [
                {
                    "sequence": 1,
                    "source_time": 0,
                    "type": "metric",
                    "payload": {"name": "tick", "value": 0},
                }
            ],
        )
    assert "sensitive-fixture" not in str(error.value)


def test_redirects_are_refused_before_forwarding_headers(endpoint):
    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)
            self.send_header("Location", endpoint.url + "/token-destination")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = sdk.ArenaAdapter(f"http://127.0.0.1:{server.server_port}", token="fixture-token")
        with pytest.raises(sdk.AdapterError, match="HTTP 302"):
            client.publish("r", "r", [{"sequence": 1}])
        assert not endpoint.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_install_is_isolated_and_refuses_existing_destinations(tmp_path, monkeypatch):
    wheel = tmp_path / "arena_intelligence-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"test artifact")
    uv = tmp_path / "uv"
    uv.write_text("fixture")
    destination = tmp_path / "local"
    calls = []
    monkeypatch.setattr(installer, "run", lambda command, **kwargs: calls.append(command))
    args = installer.options(
        ["--wheel", str(wheel), "--destination", str(destination), "--uv", str(uv)]
    )
    receipt = installer.install(args)
    assert receipt["status"] == "installed"
    assert receipt["native_engine"] is None
    assert len(calls) == 3
    assert calls[0] == [str(uv), "venv", "--python", "3.13", str(destination / "venv")]
    assert calls[1][-1] == str(wheel)
    assert "--python" in calls[1]
    launcher = (destination / "start.py").read_text()
    assert "serve" in launcher and "8767" in launcher
    assert str(destination / "workspace") in launcher
    assert (destination / "integrations" / "simulation_adapter.py").is_file()
    with pytest.raises(installer.InstallError, match="already exists"):
        installer.install(args)
    assert len(calls) == 3


def test_failed_install_preserves_receipt_and_requires_explicit_native_source(
    tmp_path, monkeypatch
):
    wheel = tmp_path / "arena.whl"
    wheel.write_bytes(b"test artifact")
    uv = tmp_path / "uv"
    uv.write_text("fixture")
    args = installer.options(
        [
            "--wheel",
            str(wheel),
            "--destination",
            str(tmp_path / "local"),
            "--uv",
            str(uv),
            "--with-native",
        ]
    )
    with pytest.raises(installer.InstallError, match="native-source"):
        installer.install(args)
    assert not args.destination.exists()
    args.with_native = False

    def fail(*args, **kwargs):
        raise installer.InstallError("fixture failure")

    monkeypatch.setattr(installer, "run", fail)
    with pytest.raises(installer.InstallError):
        installer.install(args)
    receipt = json.loads((args.destination / "installation.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["failure"] == "InstallError"


def test_sample_initial_and_terminal_events_match_the_real_api_schema():
    from arena_intelligence.product_models import TelemetryBatch

    for index in (0, 1, 23):
        envelope = {
            "schema_version": 1,
            "scope": "synthetic_simulation",
            "session": {
                "external_id": "sample-contract",
                "name": "Synthetic fixture",
                "status": "completed" if index == 23 else "running",
            },
            "events": sample.synthetic_batch(
                index, source_time=float(index), sequence=index * 5 + 1, final=index == 23
            ),
        }
        validated = TelemetryBatch.model_validate_json(json.dumps(envelope))
        assert len(validated.events) == len(envelope["events"])
        snapshot = next(event.payload for event in validated.events if event.type == "snapshot")
        report = next(
            event.payload["records"][0] for event in validated.events if event.type == "evidence"
        )
        reported_entity = next(
            entity for entity in snapshot["entities"] if entity["id"] == "reported-0"
        )
        assert reported_entity["source_time"] == report["observed_at"]
        assert (reported_entity["x"], reported_entity["y"]) == (
            report["details"]["x"],
            report["details"]["y"],
        )
        assert {node["id"] for node in snapshot["world"]["nodes"]} == {0, 1, 2}


def test_packaged_reference_pair_has_matched_conditions_and_causal_citations():
    from arena_intelligence.product_models import TelemetryEvent

    directory = ROOT / "src" / "arena_intelligence" / "assets" / "reference-pair"
    assets = [
        json.loads((directory / f"scenario-004-{mode}.json").read_text())
        for mode in ("single", "multi")
    ]
    assert assets[0]["replay"]["comparison_context"] == assets[1]["replay"]["comparison_context"]
    assert isinstance(assets[0]["replay"]["comparison_context"], dict)
    assert len(assets[0]["replay"]["comparison_context"]["scenario_fingerprint"]) == 64
    assert assets[0]["source_run_id"] != assets[1]["source_run_id"]
    assert {asset["backend"] for asset in assets} == {"single", "multi"}
    for asset in assets:
        assert asset["source_kind"] == "recorded_model_reference"
        assert not {"states", "map", "observations"} & asset["replay"].keys()
        assert len(json.dumps(asset).encode()) < 12 * 1024 * 1024
        evidence = set()
        roles = set()
        assessments = 0
        for index, value in enumerate(asset["events"]):
            event = TelemetryEvent.model_validate(value)
            assert event.sequence == index
            if event.type == "snapshot":
                roles.add(event.payload["visibility"])
                if event.payload["visibility"] == "observer":
                    assert event.payload["agent_cutoff_time"] <= event.source_time
                    own = [
                        entity
                        for entity in event.payload["entities"]
                        if entity["team"] == "friendly"
                    ]
                    assert len(own) == asset["replay"]["config"]["bots_per_team"]
                    assert all(entity["objective_id"] in {0, 1, 2} for entity in own)
            elif event.type == "evidence":
                assert event.payload["available_source_time"] == event.source_time
                assert all(
                    record["received_at"] <= event.source_time
                    for record in event.payload["records"]
                )
                evidence.update(record["id"] for record in event.payload["records"])
            elif event.type == "assessment":
                assert event.payload["origin"] == "model"
                assert (
                    len(event.payload["assignments"]) == asset["replay"]["config"]["bots_per_team"]
                )
                assert event.payload["accepted"] is True
                assert event.payload["application_source_time"] > event.source_time
                for hypothesis in event.payload["hypotheses"]:
                    assert set(hypothesis["evidence_ids"]) <= evidence
                assessments += 1
        assert roles == {"observer", "evaluator"}
        assert assessments == 6
        assert asset["replay"]["summary"]["planning_opportunities"] == 6
        assert all(
            len(item["sha256"]) == 64 and item["bytes"] > 0
            for item in asset["source_checksums"].values()
        )
