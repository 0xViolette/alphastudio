"""Shared command-line and output helpers for performance reports."""

import argparse
import sys
from dataclasses import replace
from datetime import date
from math import isfinite
from pathlib import Path

import costs as cost_cli
from config import PLOTS, TEARSHEETS, TRADE_LOGS
from engine import DEFAULT_STRATEGY, STRATEGIES
from html_report import write_html_report
from plot import write_performance_plot
from report import build_report
from terminal import print_report


def ask_strategy(default=DEFAULT_STRATEGY):
    names = list(STRATEGIES)
    if len(names) == 1:
        return names[0]
    print("\nStrategy (research/strategies/*.ml):")
    for i, name in enumerate(names, 1):
        mark = " (default)" if name == default else ""
        print(f"  {i}. {name}{mark}")
    default_i = names.index(default) + 1
    while True:
        try:
            raw = input(f"choose 1-{len(names)} [{default_i}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit("strategy picker cancelled")
        if not raw:
            return default
        if raw in STRATEGIES:
            return raw
        try:
            index = int(raw)
        except ValueError:
            index = 0
        if 1 <= index <= len(names):
            return names[index - 1]
        print(f"  enter 1-{len(names)} or a strategy name", file=sys.stderr)


def resolve_strategy(name=None, *, prompt=None):
    if name is not None:
        return name
    if prompt is None:
        prompt = sys.stdin.isatty()
    return ask_strategy() if prompt else DEFAULT_STRATEGY


def date_arg(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def add_report_arguments(parser, *, stem, meta=None, data_path=None):
    parser.add_argument(
        "strategy", nargs="?", choices=list(STRATEGIES), default=None,
        help=f"which strategy to run (default: {DEFAULT_STRATEGY}; prompts on a TTY)",
    )
    parser.add_argument("--out", type=Path, default=PLOTS / f"{stem}.png")
    parser.add_argument(
        "--initial", type=float, default=None,
        help="starting account balance for engine and report (default: cost config)",
    )
    parser.add_argument("--rf", type=float, default=0.0)
    parser.add_argument("--mar", type=float, default=0.0)
    parser.add_argument(
        "--trials", type=int, default=0,
        help="number of strategy variants tried; deflates the Sharpe ratio",
    )
    parser.add_argument(
        "--export", nargs="?", type=Path,
        const=TEARSHEETS / f"{stem}_report.html", default=None, metavar="PATH",
        help="write a self-contained HTML report instead of printing statistics",
    )
    parser.add_argument(
        "--csv", nargs="?", type=Path,
        const=TRADE_LOGS / f"{stem}_trades.csv", default=None, metavar="PATH",
        help="write a CSV trade log",
    )
    cost_cli.add_arguments(parser, meta=meta, data_path=data_path)


def normalize_dates(args):
    args.date_from = args.from_date
    args.date_till = args.till_date
    if isinstance(args.date_from, str):
        args.date_from = date_arg(args.date_from)
    if isinstance(args.date_till, str):
        args.date_till = date_arg(args.date_till)


def sync_cost_dates(args):
    args.from_date = args.date_from.isoformat() if args.date_from else None
    args.till_date = args.date_till.isoformat() if args.date_till else None


def prompt_date_window(args):
    def ask_date(prompt):
        while True:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            try:
                raw = sys.stdin.readline()
            except (EOFError, OSError):
                return None
            if raw == "":
                return None
            raw = raw.strip()
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                print("Please enter a date in YYYY-MM-DD format, or press Enter.")

    if args.date_from is None or args.date_till is None:
        print("\nREPORT DATE WINDOW")
        print("Enter dates as YYYY-MM-DD. Press Enter to use the full available range.")
        if args.date_from is None:
            args.date_from = ask_date("From date (Enter = earliest available): ")
        if args.date_till is None:
            args.date_till = ask_date("To date   (Enter = latest available): ")


def prompt_output_files(args, *, stem):
    if not sys.stdin.isatty():
        return

    def ask(prompt):
        try:
            return input(prompt)
        except (EOFError, OSError):
            return ""

    default_png = PLOTS / f"{stem}.png"
    if args.out == default_png and "--out" not in sys.argv:
        print("\nOUTPUT FILES")
        raw = ask(f"PNG file name (Enter = {default_png.name}): ").strip()
        if raw:
            args.out = Path(raw)
    if args.csv is None and "--csv" not in sys.argv:
        raw = ask("Export trades CSV? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = ask(f"  CSV file name (Enter = {stem}_trades.csv): ").strip()
            args.csv = Path(name) if name else TRADE_LOGS / f"{stem}_trades.csv"
    if args.export is None and "--export" not in sys.argv:
        raw = ask("Export HTML report? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = ask(f"  HTML file name (Enter = {stem}_report.html): ").strip()
            args.export = Path(name) if name else TEARSHEETS / f"{stem}_report.html"
    if args.date_from and args.date_till and args.date_from > args.date_till:
        sys.exit("--from must be on or before --till")


def select_date_window(date_from, date_till, bars):
    available_from = min(bar.dt.date() for bar in bars)
    available_till = max(bar.dt.date() for bar in bars)
    date_from = date_from or available_from
    date_till = date_till or available_till
    print(f"\nAvailable data: {available_from} to {available_till} (inclusive).")
    if date_from > date_till:
        sys.exit("--from must be on or before --till")
    indices = [
        i for i, bar in enumerate(bars)
        if date_from <= bar.dt.date() <= date_till
    ]
    if not indices:
        sys.exit(
            f"no bars in requested date range {date_from} to {date_till}; "
            f"available data is {available_from} to {available_till}"
        )
    print(f"Reporting window: {bars[indices[0]].dt.date()} to {bars[indices[-1]].dt.date()}\n")
    return indices


def resolve_initial(args, cfg):
    if args.initial is not None:
        if args.initial_balance is not None and args.initial != args.initial_balance:
            sys.exit("--initial and --initial-balance must match when both are supplied")
        cfg = replace(cfg, initial_balance=args.initial)
    initial = cfg.initial_balance
    if not isfinite(initial) or initial <= 0:
        sys.exit("initial balance must be finite and positive")
    source = (
        "command line"
        if args.initial is not None or args.initial_balance is not None
        else "cost configuration"
    )
    return cfg, source


def emit_report(
    result,
    args,
    *,
    title,
    equity_label,
    benchmark_label,
    script_label,
    include_costs=True,
):
    content = build_report(result.metrics)
    if not include_costs:
        content["sections"] = [
            section for section in content["sections"] if section[0] != "COSTS"
        ]
    if args.export is None:
        print_report(content)
    write_performance_plot(
        result,
        args.out,
        title=title,
        equity_label=equity_label,
        benchmark_label=benchmark_label,
        script_label=script_label,
    )
    print(f"plot saved to:  {args.out}")
    if args.export is not None:
        write_html_report(
            args.export, result.metrics, content, args.out.read_bytes(), script_label
        )
        print(f"report saved to: {args.export}")
