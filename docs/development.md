# Development and experiments

Build, test, and reproduce Arena from a source checkout. For a packaged installation, see [Install Arena](install.md).

## Develop the frontend

The typed React application lives in `frontend/`. Node 22.12+ or 24 is required only for frontend development:

```sh
cd frontend
npm ci
npm run build
```

The build type-checks the source and writes the browser bundle into `src/arena_intelligence/assets/app/` for Python packaging. The application loads its fonts and assets locally. Use the local Python service for the API; `npm run dev` proxies to port 8767.

## Build the native simulator

To run new native arena matches, install a C++20 compiler, CMake 3.24+, Ninja, Python 3.11+, and uv. The project was developed on an Apple M2 Pro with macOS 14.4.1. CMake/Ninja can be installed with your package manager; `uv tool install cmake` also provides CMake.

```sh
uv sync --frozen --group dev
cmake -S . -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/release
uv run arena run --engine build/release/arena-sim --backend mock --opponent switch --run-dir runs/demo
uv run arena replay runs/demo --engine build/release/arena-sim
```

The default mock run advances a complete 180-second virtual match quickly without API calls. The app can also launch a native mock session when the engine is available. Its replay HTML is a standalone artifact: open it in a browser to play/scrub the map, inspect assignments, and follow evidence references. The observer view shows received sightings and their age; the evaluator truth view is labeled separately.

Add `--paced` to run at ten ticks per wall-clock second. For example, test slow inference with `--mock-delay 10 --cycle-timeout 20`. For a short infrastructure smoke run, add `--duration-ticks 40 --planning-ticks 0`; a short run does not reach the normal forecast horizon and its report marks those forecasts uncovered.

## Use Claude in native arena runs

Copy `.env.example` to a private `.env` and set `ANTHROPIC_API_KEY`. Organization-scoped keys also need `ANTHROPIC_WORKSPACE_ID` from Claude Console Settings → Workspaces. `.env` is ignored by Git. The SDK receives the key through the environment; it is never placed in simulation records or prompts.

```sh
uv run --env-file .env arena run --engine build/release/arena-sim --backend multi --paced --allow-paid --budget-bucket dev --run-dir runs/claude-pilot
```

The default model is `claude-sonnet-5-5`. A two-agent cycle has at most two calls with 4,096 output tokens each; the single-agent baseline receives an equivalent aggregate output allowance. A whole cycle has a 20-second deadline. SDK automatic retries are disabled. Structured JSON is also checked locally for legal assignments, probability sums, evidence IDs, and timestamps.

The persistent ledger defaults to `.arena/costs.sqlite3`. It enforces a $55 project ceiling and dev/eval/demo buckets of $10/$35/$10, retaining worst-case reservations for ambiguous failures. This guard covers this project's requests; it does not read the account balance or spending from other applications. Use the same ledger across runs. `ARENA_PROJECT_CEILING_USD` can persist a lower ceiling; it cannot raise the hard $55 cap. The stored pricing is documented in the budget module and must be reviewed before changing model configuration.

## Game and reasoning

- A 32×32 grid has two teams of six bots and three resource nodes. Teams earn points by having more bots within Manhattan distance two of a node.
- The engine uses fixed integer ticks and deterministic A*. Bots move once every five ticks and may overlap. Generated symmetric maps keep objectives reachable.
- Terrain is known; opposing bots are visible within six cells of friendly bots. Reports can be dropped or delayed. Each sighting retains observation and delivery timestamps. Node counts include only sensor-visible opponents and are lower bounds.
- Opponents either select the nearest objective, hold selected objectives, or switch priorities midmatch. Their policy identity is hidden from the agents.
- Both live configurations receive the same three initial evidence summaries and public game rules in a bounded compact context. The analyst proposes hypotheses, alternative explanations, forecasts, and up to three follow-up evidence checks. Local checks read permitted history. The challenger revises the assessment using the check results.
- C++ validates and applies a complete friendly assignment at a tick boundary. Expired advice and explicitly reported cycle failures fall back to the same local nearest-objective controller used by the baseline.

Each prediction concerns the opponent's most occupied node 30 simulated seconds after the observation cutoff. Recommendations can arrive later, reducing the remaining forecast horizon. Coverage includes every scheduled opportunity, including missing, rejected, superseded, and expired outputs. Model explanations are inspected alongside testable forecasts; citing an event ID alone does not prove a claim.

## Tests and measurements

```sh
ctest --test-dir build/release --output-on-failure
ARENA_ENGINE="$PWD/build/release/arena-sim" uv run pytest -q
uv run arena benchmark --engine build/release/arena-sim --output reports/native-benchmark.json
uv run arena evaluate --matrix configs/heldout-free.json --engine build/release/arena-sim --runs-dir runs/heldout-final --output reports/heldout-free.json
```

The free matrix contains four frozen map seeds, three opponent policies, and both starting sides: 24 conditions each for frequency and mock backends. It validates the evaluation machinery and supplies local baselines. Mock results cannot establish that LLM reasoning improves strategy.

After measuring pilot token usage, the paid comparison uses the same conditions:

```sh
uv run --env-file .env python tools/run_study.py --jobs 8 --allow-paid
uv run --env-file .env python tools/compare_observations.py --jobs 4 --allow-paid
```

The paid matrix runs 48 paced matches of three minutes each, with one cycle in flight per match and eight matches in parallel in the supplied command. The matched-input benchmark then supplies the same six recorded observation histories to frequency, single, and multi configurations; four parallel reference streams mean at most eight paid cycles. Source/configuration hashes and concurrent load are recorded. Reusing existing run destinations is rejected; choose fresh `--run-root`/`--output` paths for another experiment. Both tools share the eval ledger bucket. Budget or API failures remain visible in coverage and run status. The comparison reports sample sizes, scores, win/draw/loss, forecast accuracy/Brier score, latency, and cost. Multiple-agent improvement is a question to measure.

The native target is p99 update time ≤5 ms for the default workload. Benchmark results distinguish native update work from model latency and real-time scheduling. Workloads of 12, 64, and 256 bots test scaling. See [the measured engineering report](../reports/engineering-report.md) and [offline evaluation](../reports/heldout-free.md) for results and limitations.

The CI workflow is configured for Linux/macOS builds, offline native/Python checks, and Linux AddressSanitizer/UndefinedBehaviorSanitizer tests. [Hosted verification](https://github.com/ysham123/Arena-Intelligence/actions/runs/37219973436) passed for the published application code; see [the commit and job record](../reports/github-ci.json). Live model checks require a local credential and are excluded from CI.

For recorded-run auditing:

```sh
python tools/verify_replays.py runs/heldout-final runs/heldout-claude examples/demo
python tools/write_engineering_report.py
```

No credential is required for replay verification. It compares every state hash, input, filtered observation, final result, and terminal tick.
