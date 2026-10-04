# Arena 0.2 product validation

Validated locally on October 4, 2026, on macOS 14.4.1 (Apple Silicon), Python 3.13.14, and Chromium. This report covers the new local product, not a repeat of the paid reasoning experiments.

## Automated checks

| Check | Result |
| --- | --- |
| Complete offline Python suite | 131 passed in 13.88 seconds |
| Frontend behavior tests | 5 passed |
| Python lint and formatting | Ruff passed; 46 files formatted |
| Frontend type check and production build | Passed |
| Frontend formatting | Prettier passed |
| Product backend and live-recording tests | Included in the complete suite; 16 focused checks passed |
| Distribution and reference-data tests | Included in the complete suite; 9 focused checks passed |

Tests cover authenticated ingestion, atomic quota rejection, duplicate retries, conflicting sequences, evidence references, observer isolation, source/availability timestamps, persistent sessions, worker shutdown, replay content security, and partial live-recording writes. Inference tests mock the provider and exercise budget reservations and failure behavior without paid calls.

The first source-environment attempts encountered a macOS FileProvider problem: native executables under Documents stalled before startup, and an SDK module imported without its contents. The successful complete run used dependencies installed under `/private/tmp` and a byte-identical copy of the native engine outside Documents. The source itself remained the current project source. No test was skipped to obtain the passing result.

## Product flows exercised

- A clean wheel installation starts without a C++ compiler, Node build, provider credential, or source checkout. Bundled JavaScript, CSS, fonts, and reference sessions are served locally.
- Two packaged reference sessions load and compare their actual recorded results. The single/multi pair reports friendly scores 266/262, forecast accuracy 4/6 for both, 100% coverage, mean inference latency 5.58/12.20 seconds, and cost $0.104/$0.231. Matching conditions do not establish matched later observations or a causal model advantage.
- Creating a connection enforces its required name. Its token is masked initially, can be revealed or copied, disappears when setup is closed, and is not written to browser local storage.
- The supplied SDK streams synthetic events into the HTTP receiver. Accepted records survive service restart; identical retries are idempotent, while a changed record with the same sequence is rejected.
- The native launch action completed a free, paced 90-second run: 900 simulation ticks, 181 observer playback frames, 367 source events, three scripted assessments, and a recorded score of 177–79. New live sessions keep a valid information view while waiting for their first frames.
- A review link opens the 90-second cutoff of the recorded 88% forecast miss. Its evidence citations resolve to individual source records. Evidence e1242 shows observed time 88 seconds and delivery/availability time 90 seconds; workspace receipt is separately labeled.
- Observer telemetry is the initial native view. Evaluator ground truth requires an explicit selection. Post-run forecast outcomes are labeled as evaluator results.
- Desktop (1440 pixels), tablet (768 pixels), and narrow mobile (390 pixels) layouts were exercised. The tablet and mobile pages have no page-wide horizontal overflow. Comparison tables scroll within their container.
- Playback, evidence inspection, run comparison, modal dismissal, and mobile navigation were exercised in the browser. Fonts loaded locally, the inspected pages requested no external resources, and the final root browser session reported zero console warnings or errors.

## Scope and remaining limits

Arena 0.2 is a local, single-user workspace for synthetic simulation data. Its HTTP contract is working; named ROS, Unreal, Isaac Sim, or commercial-system adapters are not implemented. The distributable wheel includes the product and reference data. Running new C++ matches requires the optional native build.

The analyst/challenger pipeline and its cost ledger remain available for native runs. No additional paid inference was used for this redesign. The new paid launch UI was checked for explicit opt-in and capability gating, but was not subjected to a fresh paid end-to-end call. Historical evaluation results remain in the separate engineering report with their original methods and limits.

Local diagnostics use documented rules over supplied observations. They do not inherit the native model benchmark's measured accuracy. Native recording updates appear when its writer flushes records; external adapters can stream continuously. Live readers preserve complete lines while a trailing write is unfinished, and completed imports remain strict.

The installer and browser flows were executed on macOS. [GitHub CI](https://github.com/ysham123/Arena-Intelligence/actions/runs/37219973436) subsequently passed Linux and macOS native/Python checks, the frontend build and tests, and Linux AddressSanitizer/UndefinedBehaviorSanitizer checks for the published application code. The tested commit and completed jobs are recorded in [github-ci.json](github-ci.json). A Windows installation remains unverified. This is not a multi-user hosted service or a certified operational system.
