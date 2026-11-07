"""The analytics console command and its interactive wizard."""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import date
from pathlib import Path

from .backtest import ROOT, BacktestError, available_strategies, run_backtest
from .costs import CostConfig, schema, synthetic_schema
from .report import build_report, chart_payload, print_report, write_html, write_trade_log
from .scenarios import make_scenario, run_scenario

SCENARIOS = ("futures", "spot", "synthetic")
REPORTS = ROOT / "reports"


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _add_cost_arguments(parser, meta=None):
    parser.add_argument("--strategy", choices=available_strategies())
    presets = [item["name"] for item in meta["presets"]] if meta else [
        "none", "futures", "synthetic_futures", "simple", "costs"
    ]
    parser.add_argument("--costs", choices=presets)
    parser.add_argument("--from", dest="from_date", type=_date)
    parser.add_argument("--till", dest="till_date", type=_date)
    parser.add_argument("--lot-size", type=float)
    parser.add_argument("--initial-balance", type=float)
    for knob in (meta or schema())["knobs"]:
        parser.add_argument(
            f"--{knob['key'].replace('_', '-')}",
            dest=knob["key"],
            type=float,
        )


def _add_analysis_arguments(parser):
    parser.add_argument("--rf", type=float)
    parser.add_argument("--mar", type=float)
    parser.add_argument("--trials", type=int)


def build_parser():
    parser = argparse.ArgumentParser(prog="analytics", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    report = commands.add_parser("report", help="run a backtest and analyze it")
    report.add_argument("kind", choices=SCENARIOS, nargs="?")
    _add_cost_arguments(report)
    _add_analysis_arguments(report)
    report.add_argument("--signals", choices=("futures", "options"))
    report.add_argument("--ohlc", choices=("full", "close-only", "close", "extrema"))
    report.add_argument("--output", type=Path, help="write a self-contained HTML report")
    report.add_argument("--trade-log", type=Path, help="write a trade CSV")
    report.add_argument("--write", action="store_true", help="write both reports at standard paths")
    report.add_argument("--chart", action="store_true", help="include interactive candles in HTML")

    benchmark = commands.add_parser("benchmark", help="measure strategy execution time")

    _add_cost_arguments(benchmark, schema())
    checks = commands.add_parser("check-signals", help="cross-check OCaml indicator output")
    _add_cost_arguments(checks, schema())
    return parser


def _choose(label, choices, default=None):
    print(f"\n{label}")
    for index, choice in enumerate(choices, 1):
        marker = " (default)" if choice == default else ""
        print(f"  {index}. {choice}{marker}")
    while True:
        prompt = f"Choose 1-{len(choices)}" + (f" [{default}]" if default else "") + ": "
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        if raw in choices:
            return raw
        try:
            index = int(raw)
            if 1 <= index <= len(choices):
                return choices[index - 1]
        except ValueError:
            pass
        print("Enter a listed name or number.")


def _optional_date(label):
    while True:
        raw = input(f"{label} (YYYY-MM-DD, Enter = full range): ").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            print("Enter a valid YYYY-MM-DD date.")


def _number(label, current, default, *, minimum=None, positive=False, integer=False):
    if current is not None:
        return current
    while True:
        raw = input(f"{label} [{default:g}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError
            if integer and not value.is_integer():
                raise ValueError
            if minimum is not None and value < minimum:
                raise ValueError
            if positive and value <= 0:
                raise ValueError
            return int(value) if integer else value
        except ValueError:
            print("Enter a valid number in the permitted range.")


def _yes_no(label):
    return input(f"{label} [y/N]: ").strip().lower() in ("y", "yes")


def _wizard(args, meta):
    kind = getattr(args, "kind", None)
    if hasattr(args, "strategy") and args.strategy is None:
        args.strategy = _choose("Strategy", available_strategies())
    if kind == "synthetic" and args.signals is None:
        args.signals = _choose("Signal source", ("futures", "options"), "options")
    if kind == "spot" and args.ohlc is None:
        args.ohlc = _choose("Spot OHLC", ("full", "close-only"), "full")
    if kind == "synthetic" and args.signals == "options" and args.ohlc is None:
        args.ohlc = _choose("Synthetic OHLC", ("close", "extrema"), "close")

    if args.costs is None:
        args.costs = _choose(
            "Cost model",
            [item["name"] for item in meta["presets"]],
            meta["default_preset"],
        )
    for knob in meta["knobs"]:
        if args.costs in knob["applies_to"]:
            value = _number(
                knob["label"],
                getattr(args, knob["key"]),
                knob["default"],
                minimum=knob["min"],
            )
            setattr(args, knob["key"], value)
    if args.from_date is None:
        args.from_date = _optional_date("From date")
    if args.till_date is None:
        args.till_date = _optional_date("Till date")

    if hasattr(args, "output") and args.output is None:
        raw = input("HTML report path (Enter = terminal only): ").strip()
        if raw:
            args.output = Path(raw)
    if hasattr(args, "output") and args.output is not None and not args.chart:
        args.chart = _yes_no("Include interactive candles?")
    if hasattr(args, "trade_log") and args.trade_log is None:
        raw = input("Trade-log CSV path (Enter = none): ").strip()
        if raw:
            args.trade_log = Path(raw)


def _cost_config(args, meta):
    preset = args.costs or meta["default_preset"]
    valid_presets = {item["name"] for item in meta["presets"]}
    if preset not in valid_presets:
        raise ValueError(f"cost model {preset!r} is not available for this scenario")
    values = {}
    for knob in meta["knobs"]:
        if preset not in knob["applies_to"]:
            continue
        value = getattr(args, knob["key"])
        value = knob["default"] if value is None else value
        if not math.isfinite(value) or value < knob["min"]:
            raise ValueError(f"{knob['key']} must be finite and >= {knob['min']:g}")
        values[knob["key"]] = float(value)

    lot_size = args.lot_size if args.lot_size is not None else float(meta["lot_size"])
    initial = args.initial_balance if args.initial_balance is not None else float(meta["initial_balance"])
    if not math.isfinite(lot_size) or lot_size <= 0:
        raise ValueError("lot size must be finite and positive")
    if not math.isfinite(initial) or initial <= 0:
        raise ValueError("initial balance must be finite and positive")
    if args.from_date and args.till_date and args.from_date > args.till_date:
        raise ValueError("--from must be on or before --till")
    return CostConfig(
        preset=preset,
        values=values,
        lot_size=lot_size,
        initial_balance=initial,
        from_date=args.from_date.isoformat() if args.from_date else None,
        till_date=args.till_date.isoformat() if args.till_date else None,
    )


def _run_report(args):
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive and args.kind is None:
        args.kind = _choose("Scenario", SCENARIOS)
    if args.kind is None:
        raise ValueError("choose futures, spot, or synthetic")
    meta = synthetic_schema() if args.kind == "synthetic" else schema()
    if interactive:
        _wizard(args, meta)
    if args.strategy is None:
        raise ValueError("--strategy is required for noninteractive reports")
    scenario = make_scenario(
        args.kind,
        signals=args.signals or "options",
        ohlc=args.ohlc,
    )
    output = args.output
    trade_log = args.trade_log
    if args.write:
        directory = REPORTS / scenario.slug
        output = output or directory / f"{args.strategy}_report.html"
        trade_log = trade_log or directory / f"{args.strategy}_trades.csv"
    if args.chart and output is None:
        raise ValueError("--chart requires --output or --write")
    config = _cost_config(args, meta)
    trials = args.trials if args.trials is not None else 0
    if trials < 0:
        raise ValueError("--trials must be non-negative")
    result = run_scenario(
        scenario,
        strategy=args.strategy,
        config=config,
        rf=args.rf if args.rf is not None else 0.0,
        mar=args.mar if args.mar is not None else 0.0,
        trials=trials,
        initial_source="command line" if args.initial_balance is not None else "cost configuration",
    )
    content = build_report(result.performance)
    print_report(content)
    if output:
        write_html(
            output,
            content,
            chart=chart_payload(result, include_candles=args.chart),
        )
        print(f"HTML report: {output}")
    if trade_log:
        write_trade_log(trade_log, result.trade_log)
        print(f"Trade log: {trade_log}")


def _run_benchmark(args):
    if sys.stdin.isatty() and sys.stdout.isatty():
        _wizard(args, schema())
    if args.strategy is None:
        raise ValueError("--strategy is required for noninteractive benchmarks")
    config = _cost_config(args, schema())
    scenario = make_scenario("futures")
    started = time.perf_counter()
    run_backtest(
        args.strategy,
        config.argv(),
        emitter=scenario.emitter,
        parse_output=False,
    )
    print(f"strategy: {args.strategy}\nwall time: {time.perf_counter() - started:.3f}s")


def _run_check_signals(args):
    from .diagnostics import check_signals

    check_signals(_cost_config(args, schema()))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            parser.print_help()
            return
        args = parser.parse_args(["report"])
    try:
         if args.command == "report":
             _run_report(args)
         elif args.command == "benchmark":
             _run_benchmark(args)
         else:
             _run_check_signals(args)
    except (BacktestError, ValueError, OSError) as exc:
        parser.exit(1, f"analytics: {exc}\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "analytics: wizard cancelled\n")


if __name__ == "__main__":
    main()
