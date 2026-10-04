# Engineering and experiment report

These measurements concern engine version 0.1.0 and the recorded fictional arena. Live reasoning uses Claude Sonnet 5.5 with compact-v1 context, medium effort, and a 20-second whole-cycle deadline. Results are exploratory; they do not establish general performance outside these scenarios.

## Native update performance

Hardware: Apple M2 Pro, 16 GiB memory, macOS-14.4.1-arm64-arm-64bit-Mach-O. Compiler: Apple LLVM 15.0.0 (clang-1500.3.9.4). Release C++20 build with CMake/Ninja.

A workload executes 1,200 native ticks, discards the first 100, and computes quantiles from the remaining 1,100 steady-clock measurements. Three complete repetitions are saved; the table uses the first, rather than selecting the fastest. Measurements cover Engine::step, including movement, sensor-report collection/delivery, and scoring. They exclude map construction, cached A* route construction, JSON serialization, pipe transport, record writing, and inference. The benchmark runs scripted nearest-objective control. Paced study processes were also present; no exclusive CPU isolation is claimed.

| Total bots | Samples | p50 µs | p95 µs | p99 µs | Maximum µs | Peak process RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 | 1100 | 0.041 | 5.625 | 6.958 | 44.708 | 1.672 |
| 64 | 1100 | 0.041 | 26.125 | 30.167 | 33.792 | 1.922 |
| 256 | 1100 | 0.041 | 132.291 | 148.959 | 231.333 | 2.078 |

The default workload meets the p99 ≤5 ms native-update target in these measurements. Very small p50 values reflect ticks without movement or scoring and timer precision; they are not end-to-end response latency. RSS is getrusage's process high-water mark and is cumulative across workloads within each process. It is not the entire Python/model/viewer memory footprint. See [raw benchmark](native-benchmark.json), [repeat 2](native-benchmark-repeat-2.json), and [repeat 3](native-benchmark-repeat-3.json).

Routing is intentionally moved out of the update loop: deterministic A* next steps are precomputed for reachable terrain cells and the three fixed destinations. Integer state, stable traversal/tie rules, and recorded external-input ticks permit exact replay. Fixed terrain and a small objective set make this tradeoff practical; dynamic terrain would require route invalidation or a different planner.

## Asynchronous failure recovery

The following paced runs use delayed mocks and make zero model calls. They exercise the same persistent native subprocess and recorded response boundary as live inference.

| Injected delay | Cycle result | Native ticks | Wall time s | Assessment / fallback application tick | Replay states |
| --- | --- | ---: | ---: | --- | ---: |
| 10 s | valid | 150 | 15.030 | assessment 101 | 151 |
| 25 s | timeout | 250 | 25.029 | fallback 201 | 251 |

Both simulations continued at approximately 10 Hz while inference waited. The over-deadline cycle ended at 20 seconds and sent an explicit fallback that applied at the next tick. [Raw resilience results](resilience.json) retain receipt/application ticks and replay checks.

## Reasoning comparisons

Each frozen matrix uses seeds 101, 211, 307, and 401, policies nearest, holder, and switch, and both mirrored sides: 24 conditions per configuration. Each full match has six planning opportunities at 0/30/60/90/120/150 simulated seconds. Single and multi use the same native controller, public game rules, initial evidence summaries, 8,192 aggregate output-token allowance, and $5 per-cycle spending ceiling. Single cannot revise from its selected follow-up checks; the challenger can. Actual usage and cost can differ within those equal allowances.

Closed-loop assignments change later friendly positions and sensor coverage. The separate matched-input benchmark feeds byte-fingerprinted identical permitted histories to all configurations. Its labels rely on the current scripted opponents' trajectories being independent of friendly assignments, checked by native regression tests. It does not claim that recommendations were accepted or applied in a live native match.

### Closed-loop outcomes

| Configuration | Matches | Win / draw / loss | Mean score | Coverage | Forecast accuracy | Brier | Mean inference s | Recorded cost USD |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| frequency/scripted (offline) | 24 | 4 / 8 / 12 | 179.000 | 100.0% | 0.444 | 0.860 | 0.000 | 0.000000 |
| mock/scripted (offline) | 24 | 20 / 0 / 4 | 237.875 | 100.0% | 0.889 | 0.493 | 0.001 | 0.000000 |
| multi/two (live) | 24 | 21 / 0 / 3 | 288.292 | 100.0% | 0.701 | 0.509 | 11.673 | 5.206772 |
| single/single (live) | 24 | 16 / 1 / 7 | 236.708 | 98.6% | 0.866 | 0.440 | 5.736 | 2.492216 |

Offline frequency is a scripted nearest-objective baseline; mock is an explicitly labeled exploratory heuristic. Their outcomes cannot establish an LLM reasoning gain. Accuracy and Brier use covered forecasts. Coverage uses every scheduled opportunity, including invalid, rejected, expired, superseded, missing, and timed-out outputs. Full status, evidence-error, staleness, inference/application latency, and cost breakdowns remain in each JSON report.

Paired multi-minus-single friendly score difference: mean 51.583 over 24 conditions; positive in 10, equal in 0, negative in 14. This is a measured result for this run, with no significance or general superiority claim.

See [live closed-loop report](heldout-claude.md) and its [configuration/load metadata](heldout-claude-metadata.json).

### Identical-observation forecasts

| Configuration | Covered / opportunities | Accuracy | Brier | Mean latency s | Known cost USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| frequency | 144 / 144 | 0.514 | 0.539 | 0.001 | 0.000000 |
| multi | 144 / 144 | 0.833 | 0.430 | 11.587 | 5.344960 |
| single | 144 / 144 | 0.826 | 0.438 | 5.597 | 2.477936 |

See [matched-input report](matched-final.md) and [full per-opportunity results](matched-final.json).

The matched comparison gives multi 1 additional correct forecast(s) out of 144; this small difference does not establish a reliable gain. The challenger approximately doubles latency and costs more. Its stronger result here is closed-loop strategy, not a large matched forecast gain.

Repeated forecasts from a match are correlated, and mirrored conditions share map structure. A small frozen matrix and a single stochastic model run per condition limit the conclusions. Native labels and validation do not prove the truth of a natural-language hypothesis. Evidence-ID errors count invalid references, not every unsupported interpretation. Forecast probabilities are evaluated, not assumed calibrated.

## Verification and reproducibility

Release native checks cover movement cadence, shortest paths, reachable mirrored maps, visibility counterfactuals, scoring/ties, atomic assignments, stale/duplicate/previous-run responses, malformed/oversized input, bounded queue pressure, reader shutdown, blocked output failure, replay integrity, and opponent trajectory invariance. Python checks cover typed validation, evidence checks, compact-context privacy/coverage, cost reservations, worker failures, async delays, matched-input fingerprints, and viewer/report boundaries.

Local macOS AddressSanitizer/UndefinedBehaviorSanitizer native and transport checks passed with no findings; macOS leak detection was disabled. Linux/macOS offline CI and Linux sanitizer checks are configured, including Linux leak detection. They have not been run on a hosted CI service in this local delivery. Live API tests are excluded.

Release verification: 89 Python tests; 18 native cases / 76,479 assertions; 5 transport tests. See [check summary](verification-checks.json) and [post-freeze usability/presentation revisions](delivery-revisions.json).

Recorded replay audit: 102 runs, 177,052 canonical states, 590 external commands, and 590 filtered observations verified; 0 API/network calls. The final result and tick are also checked, so truncated state logs fail. See [replay audit](replay-verification.json).

Persistent ledger charged-or-reserved total: $15.740062; effective project ceiling $52.55; unreconciled requests 0. Unknown billing retains worst-case reservations. The ledger guards project calls and does not track unrelated account spending. See [final ledger summary](budget-final.json).

To reproduce: build Release, run the offline checks and frozen free matrix, then use tools/run_study.py and tools/compare_observations.py for explicitly authorized paced API studies with one shared ledger. tools/verify_replays.py replays saved runs without credentials. tools/write_engineering_report.py regenerates this report from measured artifacts. See the README for exact commands.
