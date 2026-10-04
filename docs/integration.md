# Connect a simulation

A connection gives an external simulation a local ingest endpoint and a token. Each simulation run becomes a session. Events carry source time, stable evidence IDs, and monotonic sequence numbers so the workspace can review the run and recognize repeated deliveries.

The current adapter accepts synthetic simulation telemetry. It displays imported observations and assessments; importing an assessment does not mean an Arena agent produced or validated it. Mark scripted and imported outputs accurately.

## Try the sample

Start the workspace, then run the included client:

```sh
python3 simulation_adapter.py --url http://127.0.0.1:8767
```

Run that command from the extracted local install kit. In the source tree, the script is under `tools/`; after installation, it is under `arena-local/integrations/`. It creates a `simulation-v1` connection, keeps the returned token in memory, and streams a deterministic toy simulation. It uses no LLM and spends no API credits. Snapshots, evidence, scripted assessments, metrics, and the final lifecycle event appear in its session. Source positions are fictional fixtures.

The sample sends about four events per second, below the 600-events-per-minute limit. `--interval` must be at least one second. Use `--steps 6` for a short walkthrough. Use a new external session ID for each new run; the default is generated automatically. Ctrl+C sends a stopped lifecycle event when the API remains reachable.

For an existing connection, set `ARENA_INTEGRATION_TOKEN` in the sample process's environment. The script then uses that connection instead of creating another. Tokens are shown once on connection creation and stored hashed by the server. The sample never prints or saves them.

## HTTP contract

The server uses a local origin such as `http://127.0.0.1:8767`. `GET /api/health` is a readiness check.

Create a connection:

```http
POST /api/connections
Content-Type: application/json

{"name":"My simulation","adapter":"simulation-v1","description":"Synthetic sandbox"}
```

The 201 response has `connection`, a one-time `integration_token`, and an absolute `ingest_url`. Keep the token for the adapter process. Ingest requests use `Authorization: Bearer <integration_token>`.

```json
{
  "schema_version": 1,
  "scope": "synthetic_simulation",
  "session": {
    "external_id": "run-2026-001",
    "name": "Resource-control experiment",
    "status": "running"
  },
  "events": [
    {
      "sequence": 1,
      "source_time": 0.0,
      "type": "snapshot",
      "payload": {
        "world": {"width": 32, "height": 32},
        "scores": [0, 0],
        "entities": [
          {"id": "bot-0", "label": "Scout A", "team": "friendly", "x": 5, "y": 8}
        ]
      }
    }
  ]
}
```

Send this envelope to `POST /api/ingest`. A successful response reports `session`, `accepted_events`, and `duplicate_events`.

| Event type | Payload |
| --- | --- |
| `snapshot` | `entities` with stable string IDs and finite numeric `x`/`y`; optional entity `label`, `team`, `objective_id`, and `source_time`; snapshot-level `visibility` (`reported`, `observer`, or `evaluator`), `agent_cutoff_time`, `world` dimensions/blocked cells/nodes, `scores`, and `tick` |
| `evidence` | `records` containing `id`, `kind`, `observed_at`, `received_at`, and `summary`; optional `entity_id` and permitted numeric `details`; optional payload-level `available_source_time` for when the agent received the evidence batch |
| `assessment` | `status`; optional `origin`, `request_id`, `summary`, `hypotheses`, `forecast`, `cutoff_tick`, `latency_seconds`, `cost_usd`, `assignments`, `accepted`, `application_source_time`, and `expiry_source_time` |
| `metric` | `name`, finite numeric `value`, and optional `unit` |
| `lifecycle` | `status` and optional `message` |

Evidence times are simulation seconds. Keep the original observation time when a report arrives late, and set `received_at` to its delivery time. Permitted details keys are `node_id`, `friendly_count`, `opponent_count`, `bot_id`, `x`, and `y`. A hypothesis contains `claim`, `evidence_ids`, and `alternative`; references should identify evidence previously supplied in this session. Forecasts have `horizon_tick` and four probabilities ordered node 0, node 1, node 2, and none. The sample labels its assessments with `status: "scripted"` and `origin: "scripted"`.

A batch contains 1–100 events and at most 256 KiB of JSON; each event is limited to 48 KiB. A snapshot has at most 128 entities. Each connection accepts at most 600 events per minute. Numeric values must be finite; JSON `NaN` and infinity are rejected.

Sequences increase within each external session, and `source_time` cannot move backwards. Gaps between sequence numbers are allowed. Retry an uncertain batch with the same event content and sequences: byte-equivalent canonical event JSON is idempotent. A reused sequence with changed content returns HTTP 409. Do not generate new sequences merely because a response was lost.

Finish with `session.status: "completed"` or `"stopped"`, normally alongside a lifecycle event. Finished sessions remain available for review and accept identical retries, but reject new unique events. A new experiment needs a new `external_id`.

## Use the small Python client

`arena_adapter.py` in the install kit, or `tools/arena_adapter.py` in the source tree, uses only the Python standard library. Copy it alongside your adapter or add its directory to your import path:

```python
from arena_adapter import ArenaAdapter

client = ArenaAdapter("http://127.0.0.1:8767")
connection = client.connect("My synthetic simulation")

batch = [{
    "sequence": 1,
    "source_time": 0.0,
    "type": "metric",
    "payload": {"name": "simulation_tick", "value": 0, "unit": "tick"},
}]
result = client.publish("my-run-001", "First experiment", batch)
```

Provide `token=` to reuse an existing connection. `publish()` does not change your event objects or automatically retry. Retain the exact batch until it is acknowledged. Catch `AdapterError` for connection and HTTP failures; rejected requests expose the HTTP status without printing token-bearing headers. The client allows loopback origins and refuses redirects so a connection token cannot be forwarded to another destination.

The native C++/Python boundary has a separate [protocol contract](protocol.md). An external simulation does not need to implement that native process protocol or install C++ to use this HTTP adapter.
