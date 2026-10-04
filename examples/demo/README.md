# Recorded demonstration

Open `replay.html` directly in a browser. This file embeds its recorded data, fonts, and controls and works without a server or API calls.

This is the completed 180-second Claude analyst-plus-challenger run from held-out scenario 004, seed 101. The original run ID is `175697cd7af7429fab9b93b6fa150ada`. All native and host records are copied unchanged from that run.

Scrub to 140 seconds to see the observed change from an opposing concentration at node 2 to a larger reported population near node 1. The 120-second assessment was applied at 134.2 seconds. Its evidence links show the exact observation and delivery times; the agent had delayed, incomplete reports.

The observer view shows current friendly telemetry and public scores alongside timestamped opposing reports. The separate **Evaluator ground truth** view reveals complete positions and evaluates forecasts after their horizons. The replay records what was proposed and applied; its controls do not submit new advice.

Final score: team 0 won 262–169. All 6 forecasts were covered; 4 of 6 matched the recorded outcome, with mean multiclass Brier score 0.4892. Settled model cost was $0.231402. These are measurements from one match, not evidence of an improvement over a baseline.

Reproduce the native state hashes with zero inference calls from the repository root:

```sh
uv run arena replay examples/demo
```
