# Arena protocol v1

C++ owns world state. stdin/stdout use bounded UTF-8 NDJSON; stderr is diagnostics. Every envelope has type, schema_version=1, and run_id. Ticks are integers (10 per second). Nodes are 0,1,2. Bots have consecutive integer IDs, team 0 first. Only permitted observer state crosses stdout; privileged evaluator data stays in record files.

## Lifecycle

start carries config and absolute record_dir. Config defaults: seed=42, bots_per_team=6, duration_ticks=1800, tick_hz=10, planning_ticks=[0,300,600,900,1200,1500], opponent_policy=nearest (or holder/switch), reasoner_team=0, mirrored=false, report_drop_rate=0.1, report_delay_ticks=20, paced=false. Mirroring swaps starting sides; opponent is team 1-reasoner_team.

Engine emits ready then tick-0 observation. Paced mode advances independently. Batch mode advances only on advance messages with positive ticks and emits advanced after completion. Assessments precede advancement; hosts forbid live Claude in batch. Scheduled observations appear at their cutoff ticks. stop emits stopped and exits. Normal completion emits finished and exits. EOF stops a run cleanly.

## Observation

Envelope fields: request_id, cutoff_tick, observation. Observation contains tick, team, size=32, scores, nodes (id,x,y), blocked ([x,y]), friendly (id,team,x,y,objective_id), visible_opponents and last_seen (id,team,x,y,observed_tick,evidence_id), and up to128 recent evidence records (id,tick,kind,data).

Sightings have kind=sighting with data bot_id,x,y,observed_tick,delivered_tick. Node reports use kind=node_status with data node_id,friendly_count,opponent_count,observed_tick,delivered_tick. Opponent counts include only bots within sensor range of a friendly bot; they are lower bounds, and zero does not prove an empty node. Evidence tick is the source observed_tick, not delivery time. Opponent arrays contain received reports, not omniscient positions. Agent history remembers previously delivered evidence IDs. No hidden policy labels, unseen positions, RNG state, or truth-file paths enter observations.

## Assessment

Envelope fields: request_id, cutoff_tick, expiry_tick=cutoff+450, assessment. Assessment contains:

- hypotheses:1–3 records with claim,evidence_ids,alternative. Empty IDs are for explicit prior/insufficient-evidence claims.
- forecast:horizon_tick=cutoff+300, probabilities=[node0,node1,node2,none]. Finite values in[0,1] sum to1 within1e-6.
- assignments: exactly one bot_id,objective_id pair for every friendly bot; node0–2, no duplicates.

Python analyst drafts also contain <=3 local checks with kind (objective_visits/occupancy_trends/report_timing), window_start_tick,window_end_tick. These checks read permitted history and are not part of the native assessment payload.

Validate run/request/cutoff, known permitted evidence, correct horizon/expiry, and complete legal assignments. Receipt after cutoff+200 is late. Assignment applies atomically at receipt_tick+1. assessment_result reports request_id,accepted,application_tick,reason,remaining_horizon_ticks. Rejected messages are recorded and do not stop the game. Citation IDs are bound to the permitted history at the issued request cutoff; newer observations cannot justify older responses. Validation checks reference membership and fields, not the semantic truth of free-text claims. At cutoff+450 assignments expire to nearest-objective fallback; a newer accepted assignment supersedes the previous one.

## Explicit failure fallback

On a failed reasoning cycle, the host sends a fallback envelope with request_id, cutoff_tick and a bounded reason category. Only the latest, unconsumed request with its original cutoff may request fallback; a stale failure cannot clear newer advice. The fallback_result reports accepted, application_tick and reason. An accepted fallback clears previous advice and selects nearest objectives atomically at receipt_tick+1. Failure notifications are recorded and replayed like assessments. EOF remains a clean lifecycle stop.

## Records

Each run has manifest.json (resolved config,run_id,engine/protocol version), commands.ndjson (received envelopes,receipt ticks,result/application ticks), states.ndjson (canonical complete integer state/checksum at every tick including0), observations.ndjson (exact permitted envelopes), results.json. Python adds reasoning.ndjson (bounded drafts/checks/final assessment,latency,usage). Credentials never enter logs.

Native --replay RUN_DIR reconstructs the manifest, injects recorded commands at recorded ticks, and compares state checksums with a nonzero exit on mismatch. It makes zero API calls. --benchmark returns JSON measurements for12,64,256 total bots with tick distributions and compiler metadata.

## Game and metrics

Every10 ticks each node awards one point to the team with strictly more bots within Manhattan distance2; ties give no points. Bots take one deterministic A* step every5 ticks and may overlap. Stable ordering and portable seeded randomness preserve determinism; wall-clock time changes presentation/delivery timing only.

Forecast truth is the node with the most opponent bots within distance2 at cutoff+300: lowest positive node ID breaks ties, none if all counts are zero. Evaluate post-run privileged states only. Score forecasts conditional on accepted assignments remaining active through the horizon. Coverage denominator includes every scheduled opportunity, including rejected,superseded,expired,missing and timed-out outputs. Advice superseded or expired at the horizon tick is uncovered, since it no longer controls that tick.
