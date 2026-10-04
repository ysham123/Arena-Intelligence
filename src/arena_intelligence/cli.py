"""CLI defaults to zero-cost batch runs. Paid runs require explicit opt-in."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .budget import CostLedger
from .process import native_command
from .protocol import RunConfig
from .reasoner import ClaudeProvider, Reasoner
from .runner import run_match

# Defaults follow the invocation directory in editable and installed-wheel usage.
DEFAULT_ENGINE = Path("build/release/arena-sim")
DEFAULT_LEDGER = Path(".arena/costs.sqlite3")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="arena", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run an instrumented match")
    run.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    run.add_argument("--backend", choices=["mock", "frequency", "single", "multi"], default="mock")
    run.add_argument("--run-dir", type=Path)
    run.add_argument("--paced", action="store_true")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--opponent", choices=["nearest", "holder", "switch"], default="nearest")
    run.add_argument("--bots-per-team", type=int, default=6)
    run.add_argument("--duration-ticks", type=int, default=1800)
    run.add_argument("--planning-ticks", help="comma-separated ticks; default every 300 ticks")
    run.add_argument("--mirrored", action="store_true")
    run.add_argument("--reasoner-team", type=int, choices=[0, 1], default=0)
    run.add_argument("--report-drop-rate", type=float, default=0.1)
    run.add_argument("--report-delay-ticks", type=int, default=20)
    run.add_argument("--cycle-timeout", type=float, default=20)
    run.add_argument("--mock-delay", type=float, default=0)
    run.add_argument("--budget-bucket", choices=["dev", "eval", "demo"], default="dev")
    run.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    run.add_argument("--allow-paid", action="store_true")
    run.add_argument("--no-viewer", action="store_true")
    replay = commands.add_parser("replay", help="verify every native state checksum offline")
    replay.add_argument("run_dir", type=Path)
    replay.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    replay.add_argument("--viewer", action="store_true")
    benchmark = commands.add_parser("benchmark", help="native timing distributions")
    benchmark.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    benchmark.add_argument("--output", type=Path)
    evaluate = commands.add_parser(
        "evaluate", help="post-run scoring or an explicitly budgeted matrix"
    )
    evaluate.add_argument("run_dirs", nargs="*", type=Path)
    evaluate.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    evaluate.add_argument(
        "--matrix",
        type=Path,
        help="JSON with scenarios:[RunConfig fields], backends:[mock,frequency,single,multi]",
    )
    evaluate.add_argument("--runs-dir", type=Path, default=Path("runs/evaluation"))
    evaluate.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    evaluate.add_argument("--paced", action="store_true")
    evaluate.add_argument("--allow-paid", action="store_true")
    evaluate.add_argument("--budget-bucket", choices=["dev", "eval", "demo"], default="eval")
    evaluate.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    evaluate.add_argument("--cycle-timeout", type=float, default=20)
    service = commands.add_parser("serve", help="open the local simulation engineering workbench")
    service.add_argument("--host", default="127.0.0.1")
    service.add_argument("--port", type=int, default=8767)
    service.add_argument("--data-dir", type=Path, default=Path(".arena/app"))
    service.add_argument("--engine", type=Path)
    service.add_argument("--sample-dir", type=Path)
    service.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    budget = commands.add_parser("budget", help="inspect persistent charges and reservations")
    budget.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    return root


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


async def execute(args: argparse.Namespace) -> dict:
    if args.command == "budget":
        return CostLedger(args.ledger).summary()
    if args.command == "run":
        if not 0 < args.cycle_timeout <= 20 or args.mock_delay < 0:
            raise ValueError("cycle timeout must be in (0,20], mock delay must be nonnegative")
        if args.backend in {"single", "multi"} and not (args.allow_paid and args.paced):
            raise ValueError("paid backends require both --allow-paid and --paced")
        planning = (
            [int(t) for t in args.planning_ticks.split(",")]
            if args.planning_ticks
            else list(range(0, args.duration_ticks, 300))
        )
        config = RunConfig(
            seed=args.seed,
            opponent_policy=args.opponent,
            bots_per_team=args.bots_per_team,
            duration_ticks=args.duration_ticks,
            planning_ticks=planning,
            reasoner_team=args.reasoner_team,
            mirrored=args.mirrored,
            report_drop_rate=args.report_drop_rate,
            report_delay_ticks=args.report_delay_ticks,
            paced=args.paced,
        )
        provider = None
        ledger = None
        if args.backend in {"single", "multi"}:
            ledger = CostLedger(args.ledger)
            provider = ClaudeProvider(ledger, args.budget_bucket)
        reasoner = Reasoner(args.backend, provider, args.mock_delay)
        run_dir = args.run_dir or Path("runs") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            result = await run_match(args.engine, run_dir, config, reasoner, args.cycle_timeout)
        finally:
            if provider:
                await provider.close()
        result["run_dir"] = str(run_dir.resolve())
        if ledger:
            result["budget"] = ledger.summary()
        if not args.no_viewer:
            from .viewer import generate_viewer

            result["viewer"] = str(generate_viewer(run_dir))
        return result
    if args.command == "replay":
        result = await native_command(args.engine, "--replay", str(args.run_dir.resolve()))
        if args.viewer:
            from .viewer import generate_viewer

            result["viewer"] = str(generate_viewer(args.run_dir))
        return result
    if args.command == "benchmark":
        result = await native_command(args.engine, "--benchmark")
        if args.output:
            _write_json(args.output, result)
        return result
    if args.command == "evaluate":
        run_dirs = list(args.run_dirs)
        if args.matrix:
            matrix = json.loads(args.matrix.read_text())
            scenarios = matrix.get("scenarios", [])
            backends = matrix.get("backends", ["frequency", "mock"])
            if not scenarios or len(scenarios) * len(backends) > 256:
                raise ValueError("matrix requires 1–256 scenario/backend combinations")
            if any(b not in {"frequency", "mock", "single", "multi"} for b in backends):
                raise ValueError("unknown matrix backend")
            paid = any(b in {"single", "multi"} for b in backends)
            if paid and not (args.paced and args.allow_paid):
                raise ValueError("paid matrix backends require both --paced and --allow-paid")
            if not 0 < args.cycle_timeout <= 20:
                raise ValueError("cycle timeout must be in (0,20]")
            ledger = CostLedger(args.ledger) if paid else None
            provider = ClaudeProvider(ledger, args.budget_bucket) if ledger else None
            try:
                for index, scenario in enumerate(scenarios):
                    config = RunConfig.model_validate({**scenario, "paced": args.paced})
                    for backend in backends:
                        run_dir = args.runs_dir / f"scenario-{index:03d}-{backend}"
                        await run_match(
                            args.engine,
                            run_dir,
                            config,
                            Reasoner(backend, provider),
                            args.cycle_timeout,
                        )
                        run_dirs.append(run_dir)
            finally:
                if provider:
                    await provider.close()
        if not run_dirs:
            raise ValueError("provide recorded run directories or --matrix")
        from .reporting import summarize_tournament, write_report

        summary = summarize_tournament(run_dirs)
        json_path = (
            args.output if args.output.suffix == ".json" else args.output.with_suffix(".json")
        )
        markdown_path = (
            args.output if args.output.suffix != ".json" else args.output.with_suffix(".md")
        )
        _write_json(json_path, summary)
        write_report(run_dirs, markdown_path)
        return {
            "report": str(json_path.resolve()),
            "markdown": str(markdown_path.resolve()),
            "summary": summary,
        }
    raise ValueError("unknown command")


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    try:
        if args.command == "serve":
            from .server import serve

            serve(
                args.host,
                args.port,
                data_dir=args.data_dir,
                engine=args.engine,
                sample_dir=args.sample_dir,
                ledger=args.ledger,
            )
            return
        result = asyncio.run(execute(args))
    except ValidationError as error:
        cli.error(f"schema rejected data ({error.error_count()} violations)")
    except (ValueError, FileNotFoundError) as error:
        cli.error(str(error))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        # SDK exceptions may include headers; do not print them.
        cli.error(f"{type(error).__name__}: operation failed; inspect bounded run logs")
    else:
        print(json.dumps(result, indent=2, allow_nan=False))
