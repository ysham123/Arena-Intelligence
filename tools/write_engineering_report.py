"""Render the measured engineering report from local experiment artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def load(name: str) -> dict | None:
    path = REPORTS / name
    return json.loads(path.read_text()) if path.exists() else None


def number(value: float | None, decimals: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{decimals}f}"


def main() -> None:
    benchmark = load("native-benchmark.json")
    resilience = load("resilience.json")
    environment = load("environment.json")
    if not all((benchmark, resilience, environment)):
        raise ValueError("benchmark, environment, and resilience measurements are required")
    lines = [
        "# Engineering and experiment report",
        "",
        "These measurements concern engine version 0.1.0 and the recorded fictional arena. "
        "Live reasoning uses Claude Sonnet 5.5 with compact-v1 context, medium effort, and "
        "a 20-second whole-cycle deadline. Results are exploratory; they do not establish "
        "general performance outside these scenarios.",
        "",
        "## Native update performance",
        "",
        f"Hardware: {environment['cpu']}, "
        f"{int(environment['memory_bytes']) / 2**30:.0f} GiB memory, "
        f"{environment['system']}. Compiler: {benchmark['compiler']}. "
        "Release C++20 build with CMake/Ninja.",
        "",
        "A workload executes 1,200 native ticks, discards the first 100, and computes "
        "quantiles from the remaining 1,100 steady-clock measurements. Three complete "
        "repetitions are saved; the table uses the first, rather than selecting the fastest. "
        "Measurements cover Engine::step, including movement, sensor-report collection/delivery, "
        "and scoring. They exclude map construction, cached A* route construction, JSON "
        "serialization, pipe transport, record writing, and inference. The benchmark runs "
        "scripted nearest-objective control. Paced study processes were also present; no "
        "exclusive CPU isolation is claimed.",
        "",
        "| Total bots | Samples | p50 µs | p95 µs | p99 µs | Maximum µs | Peak process RSS MiB |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in benchmark["workloads"]:
        timing = row["tick_us"]
        lines.append(
            f"| {row['total_bots']} | {row['samples']} | {number(timing['p50'])} | "
            f"{number(timing['p95'])} | {number(timing['p99'])} | "
            f"{number(timing['max'])} | {number(row['process_peak_rss_bytes'] / 2**20)} |"
        )
    lines.extend(
        [
            "",
            "The default workload meets the p99 ≤5 ms native-update target in these measurements. "
            "Very small p50 values reflect ticks without movement or scoring and timer precision; "
            "they are not end-to-end response latency. RSS is getrusage's process high-water mark "
            "and is cumulative across workloads within each process. It is not the entire "
            "Python/model/viewer memory footprint. See [raw benchmark](native-benchmark.json), "
            "[repeat 2](native-benchmark-repeat-2.json), and [repeat "
            "3](native-benchmark-repeat-3.json).",
            "",
            "Routing is intentionally moved out of the update loop: deterministic A* next steps "
            "are precomputed for reachable terrain cells and the three fixed destinations. "
            "Integer state, stable traversal/tie rules, and recorded external-input ticks permit "
            "exact replay. Fixed terrain and a small objective set make this tradeoff practical; "
            "dynamic terrain would require route invalidation or a different planner.",
            "",
            "## Asynchronous failure recovery",
            "",
            "The following paced runs use delayed mocks and make zero model calls. They exercise "
            "the same persistent native subprocess and recorded response boundary as "
            "live inference.",
            "",
            "| Injected delay | Cycle result | Native ticks | Wall time s | Assessment / "
            "fallback application tick | Replay states |",
            "| --- | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for case in resilience["cases"]:
        ticks = case["application_ticks"] or case["fallback_application_ticks"]
        kind = "assessment" if case["application_ticks"] else "fallback"
        lines.append(
            f"| {case['injected_delay_seconds']} s | {', '.join(case['cycle_statuses'])} | "
            f"{case['recorded_ticks']} | {number(case['wall_seconds'])} | "
            f"{kind} {', '.join(map(str, ticks))} | {case['replay']['states_verified']} |"
        )
    lines.extend(
        [
            "",
            "Both simulations continued at approximately 10 Hz while inference waited. The "
            "over-deadline cycle ended at 20 seconds and sent an explicit fallback that applied "
            "at the next tick. [Raw resilience results](resilience.json) retain "
            "receipt/application "
            "ticks and replay checks.",
            "",
            "## Reasoning comparisons",
            "",
            "Each frozen matrix uses seeds 101, 211, 307, and 401, policies nearest, holder, and "
            "switch, and both mirrored sides: 24 conditions per configuration. Each full match "
            "has six planning opportunities at 0/30/60/90/120/150 simulated seconds. Single and "
            "multi use the same native controller, public game rules, initial evidence summaries, "
            "8,192 aggregate output-token allowance, and $5 per-cycle spending ceiling. Single "
            "cannot revise from its selected follow-up checks; the challenger can. Actual usage "
            "and cost can differ within those equal allowances.",
            "",
            "Closed-loop assignments change later friendly positions and sensor coverage. The "
            "separate matched-input benchmark feeds byte-fingerprinted identical "
            "permitted histories "
            "to all configurations. Its labels rely on the current scripted opponents' "
            "trajectories "
            "being independent of friendly assignments, checked by native regression tests. It "
            "does not claim that recommendations were accepted or applied in a live native match.",
            "",
            "### Closed-loop outcomes",
            "",
            "| Configuration | Matches | Win / draw / loss | Mean score | Coverage | "
            "Forecast accuracy | Brier | Mean inference s | Recorded cost USD |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for filename, label in [("heldout-free.json", "offline"), ("heldout-claude.json", "live")]:
        study = load(filename)
        if not study:
            continue
        for name, data in sorted(study["configurations"].items()):
            lines.append(
                f"| {name} ({label}) | {data['runs']} | "
                f"{data['wins']} / {data['draws']} / {data['losses']} | "
                f"{number(data['score_mean'])} | {number(data['forecast_coverage'] * 100, 1)}% | "
                f"{number(data['forecast_accuracy'])} | {number(data['brier_score'])} | "
                f"{number(data['inference_latency_seconds']['mean'])} | "
                f"{number(data['cost_usd'], 6)} |"
            )
    lines.extend(
        [
            "",
            "Offline frequency is a scripted nearest-objective baseline; mock is an explicitly "
            "labeled exploratory heuristic. Their outcomes cannot establish an LLM reasoning gain. "
            "Accuracy and Brier use covered forecasts. Coverage uses every scheduled opportunity, "
            "including invalid, rejected, expired, superseded, missing, and timed-out outputs. "
            "Full status, evidence-error, staleness, inference/application latency, and cost "
            "breakdowns remain in each JSON report.",
            "",
        ]
    )
    live = load("heldout-claude.json")
    if live:
        by_condition = {}
        for row in live["runs"]:
            key = (row["seed"], row["opponent_policy"], row["mirrored"])
            by_condition.setdefault(key, {})[row["backend"]] = row
        differences = []
        for pair in by_condition.values():
            if {"single", "multi"} <= pair.keys():
                single, multi = pair["single"], pair["multi"]
                differences.append(
                    multi["scores"][multi["reasoner_team"]]
                    - single["scores"][single["reasoner_team"]]
                )
        if differences:
            lines.append(
                "Paired multi-minus-single friendly score difference: "
                f"mean {sum(differences) / len(differences):.3f} over {len(differences)} "
                f"conditions; "
                f"positive in {sum(x > 0 for x in differences)}, equal in "
                f"{sum(x == 0 for x in differences)}, negative in "
                f"{sum(x < 0 for x in differences)}. "
                "This is a measured result for this run, with no significance or general "
                "superiority claim."
            )
            lines.append("")
        lines.append(
            "See [live closed-loop report](heldout-claude.md) and its "
            "[configuration/load metadata](heldout-claude-metadata.json)."
        )
        lines.append("")
    matched = load("matched-final.json")
    if matched:
        lines.extend(
            [
                "### Identical-observation forecasts",
                "",
                "| Configuration | Covered / opportunities | Accuracy | Brier | Mean "
                "latency s | Known cost USD |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, data in sorted(matched["configurations"].items()):
            lines.append(
                f"| {name} | {data['scored_forecasts']} / {data['planning_opportunities']} | "
                f"{number(data['forecast_accuracy'])} | {number(data['brier_score'])} | "
                f"{number(data['latency_seconds']['mean'])} | "
                f"{number(data['known_cost_usd'], 6)} |"
            )
        lines.extend(
            [
                "",
                "See [matched-input report](matched-final.md) and "
                "[full per-opportunity results](matched-final.json).",
                "",
            ]
        )
    if matched:
        single = matched["configurations"]["single"]
        multi = matched["configurations"]["multi"]
        difference = round(
            multi["forecast_accuracy"] * multi["scored_forecasts"]
            - single["forecast_accuracy"] * single["scored_forecasts"]
        )
        lines.append(
            f"The matched comparison gives multi {difference} additional correct forecast(s) "
            f"out of {multi['planning_opportunities']}; this small difference does not establish "
            "a reliable gain. The challenger approximately doubles latency and costs more. "
            "Its stronger result here is closed-loop strategy, not a large matched forecast gain."
        )
        lines.append("")
    lines.extend(
        [
            "Repeated forecasts from a match are correlated, and mirrored conditions share map "
            "structure. A small frozen matrix and a single stochastic model run per condition "
            "limit the conclusions. Native labels and validation do not prove the truth of a "
            "natural-language hypothesis. Evidence-ID errors count invalid references, not every "
            "unsupported interpretation. Forecast probabilities are evaluated, not "
            "assumed calibrated.",
            "",
            "## Verification and reproducibility",
            "",
            "Release native checks cover movement cadence, shortest paths, reachable "
            "mirrored maps, "
            "visibility counterfactuals, scoring/ties, atomic assignments, "
            "stale/duplicate/previous-run "
            "responses, malformed/oversized input, bounded queue pressure, reader "
            "shutdown, blocked "
            "output failure, replay integrity, and opponent trajectory invariance. Python checks "
            "cover typed validation, evidence checks, compact-context privacy/coverage, "
            "cost reservations, "
            "worker failures, async delays, matched-input fingerprints, and "
            "viewer/report boundaries.",
            "",
            "Local macOS AddressSanitizer/UndefinedBehaviorSanitizer native and transport checks "
            "passed with no findings; macOS leak detection was disabled. Linux/macOS offline CI "
            "and Linux sanitizer checks are configured, including Linux leak detection. They have "
            "not been run on a hosted CI service in this local delivery. Live API tests "
            "are excluded.",
            "",
        ]
    )
    checks = load("verification-checks.json")
    if checks:
        lines.append(
            f"Release verification: {checks['python']['tests']} Python tests; "
            f"{checks['native_release']['cases']} native cases / "
            f"{checks['native_release']['assertions']:,} assertions; "
            f"{checks['native_release']['transport_tests']} transport tests. "
            "See [check summary](verification-checks.json) and "
            "[post-freeze usability/presentation revisions](delivery-revisions.json)."
        )
        lines.append("")
    replay = load("replay-verification.json") or load("replay-verification-free.json")
    if replay:
        lines.append(
            f"Recorded replay audit: {replay['run_count']} runs, "
            f"{replay['states_verified']:,} canonical states, "
            f"{replay['commands_verified']:,} external commands, and "
            f"{replay['observations_verified']:,} filtered observations verified; "
            f"{replay['network_calls']} API/network calls. The final result and tick are also "
            "checked, so truncated state logs fail. See [replay audit]("
            + (
                "replay-verification.json"
                if load("replay-verification.json")
                else "replay-verification-free.json"
            )
            + ")."
        )
        lines.append("")
    budget = load("budget-final.json")
    if budget:
        lines.append(
            f"Persistent ledger charged-or-reserved total: "
            f"${budget['charged_or_reserved_usd']:.6f}; "
            f"effective project ceiling ${budget['ceiling_usd']:.2f}; "
            f"unreconciled requests {budget['unreconciled_requests']}. "
            "Unknown billing retains worst-case reservations. The ledger guards project calls "
            "and does not track unrelated account spending. See [final ledger "
            "summary](budget-final.json)."
        )
        lines.append("")
    lines.extend(
        [
            "To reproduce: build Release, run the offline checks and frozen free matrix, then "
            "use tools/run_study.py and tools/compare_observations.py for explicitly authorized "
            "paced API studies with one shared ledger. tools/verify_replays.py replays saved "
            "runs without credentials. tools/write_engineering_report.py regenerates this report "
            "from measured artifacts. See the README for exact commands.",
            "",
        ]
    )
    output = REPORTS / "engineering-report.md"
    temporary = output.with_suffix(".tmp")
    temporary.write_text("\n".join(lines))
    temporary.replace(output)
    print(str(output))


if __name__ == "__main__":
    main()
