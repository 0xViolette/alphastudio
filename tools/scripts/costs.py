"""Shared cost + period configuration for the Python report/plot scripts.

The OCaml binary owns the defaults and the rate card. This module fetches its
schema with `dune exec bin/main.exe -- --schema`, prompts for a cost model and
a backtest period on a TTY, and turns the answers into flags passed after
`dune exec <target> --`. The period (--from/--till, YYYY-MM-DD, IST) defaults
to the full data range; the wizard shows the range the data actually covers.

There is no environment-variable or .env lookup: explicit flags win, otherwise
the binary's defaults are used. `--csv` readers and non-TTY runs never prompt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# the CSV the emitter (plot_pnl.EMITTER) streams; bounds are read directly
# from it so the wizard can name the usable --from/--till range
DATA_CSV = ROOT / "data" / "new_data.csv"

# knob key -> (short prompt name, unit suffix)
_PROMPT = {
    "slip_ticks": ("slippage", "ticks"),
    "slip_range_pct": ("slippage", "% of bar range"),
    "fee_per_order": ("flat fee per order", ""),
    "call_premium_pct": ("call premium", "% of underlying"),
    "put_premium_pct": ("put premium", "% of underlying"),
    "opt_slip_ticks": ("option slippage", "ticks per leg"),
    "opt_slip_pct": ("option slippage", "% of premium"),
}


@lru_cache(maxsize=1)
def schema() -> dict:
    """Defaults, presets, knobs and statutory rates from tools/costs_schema.json."""
    path = ROOT / "tools" / "costs_schema.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"could not read cost schema {path}: {exc}")


@lru_cache(maxsize=1)
def data_bounds() -> tuple[date | None, date | None]:
    """(first, last) date covered by the backtest data (the emitter's CSV).

    The CSV is chronological (protocol/ohlc.md), so the bounds are the first
    data row and the last line: two small reads, no full scan. (None, None)
    when the data cannot be read — the wizard then skips the period prompt.
    """
    try:
        with DATA_CSV.open("rb") as f:
            f.readline()  # header
            first = f.readline().split(b",", 1)[0].decode()
            f.seek(0, 2)
            pos, tail = f.tell(), b""
            while pos > 0 and tail.count(b"\n") < 2:
                step = min(pos, 4096)
                pos -= step
                f.seek(pos)
                tail = f.read(step) + tail
            last = [ln for ln in tail.split(b"\n") if ln.strip()][-1]
            last = last.split(b",", 1)[0].decode()
        to_date = lambda s: datetime.strptime(s.strip(), "%d/%m/%Y").date()
        return to_date(first), to_date(last)
    except (OSError, ValueError, IndexError):
        return None, None


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """Add --costs plus one optional flag per cost knob."""
    meta = schema()
    names = [p["name"] for p in meta["presets"]]
    ap.add_argument(
        "--costs",
        choices=names,
        default=None,
        metavar="PRESET",
        help="cost model: " + ", ".join(names) + " (default: "
        + meta["default_preset"] + "; prompts on a TTY when omitted)",
    )
    for knob in meta["knobs"]:
        key = knob["key"]
        prompt, unit = _PROMPT.get(key, (key.replace("_", " "), knob["unit"]))
        ap.add_argument(
            "--" + key.replace("_", "-"),
            type=float,
            default=None,
            metavar="F",
            help=f"{prompt}"
            + (f" ({unit.replace('%', '%%')})" if unit else "")
            + f" (default {knob['default']:g}; skips the wizard)",
        )
    ap.add_argument(
        "--lot-size",
        type=float,
        default=None,
        metavar="F",
        help=f"contract lot size (default {meta['lot_size']:g})",
    )
    ap.add_argument(
        "--initial-balance",
        type=float,
        default=None,
        metavar="F",
        help=f"starting account balance (default {meta['initial_balance']:g})",
    )
    d0, d1 = data_bounds()
    bounds = (
        f"data covers {d0.isoformat()} \u2192 {d1.isoformat()}"
        if d0 and d1
        else "data range unknown"
    )
    ap.add_argument(
        "--from",
        dest="from_date",
        default=None,
        metavar="YYYY-MM-DD",
        help=f"first day of the backtest, IST ({bounds})",
    )
    ap.add_argument(
        "--till",
        dest="till_date",
        default=None,
        metavar="YYYY-MM-DD",
        help=f"last day of the backtest, inclusive, IST ({bounds})",
    )


@dataclass(frozen=True)
class CostConfig:
    preset: str
    values: dict[str, float]
    lot_size: float
    initial_balance: float
    from_date: str | None = None  # ISO YYYY-MM-DD, None = unbounded
    till_date: str | None = None

    def argv(self) -> list[str]:
        """Flags for `dune exec <target> -- ...`."""
        out = [
            "--costs",
            self.preset,
            "--lot-size",
            _g(self.lot_size),
            "--initial-balance",
            _g(self.initial_balance),
            "--print-costs",
        ]
        for key, value in self.values.items():
            out += ["--" + key.replace("_", "-"), _g(value)]
        if self.from_date is not None:
            out += ["--from", self.from_date]
        if self.till_date is not None:
            out += ["--till", self.till_date]
        return out

    def label(self) -> str:
        """Short label for a report caption."""
        return f"{self.preset} (NSE rates as of {schema()['as_of']})"


def _g(x: float) -> str:
    return f"{x:.10g}"


def _fmt(x: float) -> str:
    return f"{x:g}"


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit("cost wizard cancelled")


def _ask_float(prompt: str, default: float, minimum: float) -> float:
    while True:
        raw = _input(f"{prompt} [{_fmt(default)}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            print("  enter a number", file=sys.stderr)
            continue
        if value < minimum:
            print(f"  must be >= {_fmt(minimum)}", file=sys.stderr)
            continue
        return value


def _parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"bad date {raw!r} (want YYYY-MM-DD)")


def _resolve_dates(args) -> tuple[str | None, str | None]:
    """Explicit --from/--till flags as ISO strings (None = open side).

    Out-of-range dates only warn: the binary clamps to the data on its own,
    so scripted runs keep working when the data file is extended.
    """
    from_d = _parse_date(args.from_date) if args.from_date else None
    till_d = _parse_date(args.till_date) if args.till_date else None
    if from_d and till_d and from_d > till_d:
        sys.exit(f"--from {from_d} is after --till {till_d}")
    d0, d1 = data_bounds()
    for flag, d in (("--from", from_d), ("--till", till_d)):
        if d is None:
            continue
        if d0 and d < d0:
            print(f"  note: {flag} {d} precedes the data start {d0}",
                  file=sys.stderr)
        if d1 and d > d1:
            print(f"  note: {flag} {d} is past the data end {d1}",
                  file=sys.stderr)
    return (
        from_d.isoformat() if from_d else None,
        till_d.isoformat() if till_d else None,
    )


def _ask_date(prompt: str, default: date) -> date:
    while True:
        raw = _input(f"  {prompt} [{default.isoformat()}]: ").strip()
        if not raw:
            return default
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("  enter a date as YYYY-MM-DD", file=sys.stderr)


def _ask_period() -> tuple[str | None, str | None]:
    """Wizard prompt for the backtest window, bounded by the data itself."""
    d0, d1 = data_bounds()
    if d0 is None or d1 is None:
        return None, None
    span = f"{d0.isoformat()} \u2192 {d1.isoformat()}"
    print(f"\nBacktest period (data covers {span}; Enter keeps the full range):")
    while True:
        from_d = _ask_date("from date", d0)
        till_d = _ask_date("till date", d1)
        if not d0 <= from_d <= d1:
            print(f"  from must be within {span}", file=sys.stderr)
            continue
        if not d0 <= till_d <= d1:
            print(f"  till must be within {span}", file=sys.stderr)
            continue
        if from_d > till_d:
            print("  from is after till", file=sys.stderr)
            continue
        return from_d.isoformat(), till_d.isoformat()


def _ask_yes_no(prompt: str) -> bool:
    while True:
        raw = _input(f"{prompt} [Y/n]: ").strip().lower()
        if raw in ("", "y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  answer y or n", file=sys.stderr)


def _ask_preset(meta: dict, default: str) -> str:
    presets = meta["presets"]
    names = [p["name"] for p in presets]
    default_i = names.index(default) + 1
    print("\nTransaction-cost model (NSE, current rates):")
    for i, p in enumerate(presets, 1):
        mark = " (default)" if p["name"] == default else ""
        print(f"  {i}. {p['name']}{mark} — {p['description']}")
    while True:
        raw = _input(f"choose 1-{len(names)} [{default_i}]: ").strip()
        if not raw:
            return default
        try:
            i = int(raw)
        except ValueError:
            i = 0
        if 1 <= i <= len(names):
            return names[i - 1]
        print(f"  enter 1-{len(names)}", file=sys.stderr)


def _applicable_knobs(meta: dict, preset: str) -> list[dict]:
    return [k for k in meta["knobs"] if preset in k["applies_to"]]


def _summary(meta: dict, cfg: CostConfig) -> None:
    print(f"\nCost model: {cfg.preset} (rates as of {meta['as_of']})")
    for knob in _applicable_knobs(meta, cfg.preset):
        key = knob["key"]
        prompt, unit = _PROMPT.get(key, (knob["label"], knob["unit"]))
        suffix = f" {unit}" if unit else ""
        print(f"  {prompt}: {_fmt(cfg.values[key])}{suffix}")
    print(f"  lot size: {_fmt(cfg.lot_size)}")
    print(f"  initial balance: {cfg.initial_balance:,.0f}")


def _build(
    args,
    meta: dict,
    preset: str,
    values: dict[str, float],
    from_date: str | None = None,
    till_date: str | None = None,
) -> CostConfig:
    lot_size = args.lot_size if args.lot_size is not None else float(meta["lot_size"])
    initial = (
        args.initial_balance
        if args.initial_balance is not None
        else float(meta["initial_balance"])
    )
    if lot_size <= 0:
        sys.exit("--lot-size must be > 0")
    if initial <= 0:
        sys.exit("--initial-balance must be > 0")
    return CostConfig(preset, values, lot_size, initial, from_date, till_date)


def resolve(
    args,
    *,
    prompt: bool | None = None,
    ask_period: bool = True,
) -> CostConfig:
    """Turn parsed args into a CostConfig, prompting only when allowed.

    - an explicit --costs or any cost knob flag skips the wizard
    - otherwise prompt on a TTY (costs, then the backtest period)
    - otherwise use the binary defaults
    - explicit --from/--till are validated and never prompted for
    - ask_period=False leaves the period to the caller (scripts/synthetic.py
      resolves it with bounds that account for the options-data coverage)
    """
    meta = schema()
    if prompt is None:
        prompt = sys.stdin.isatty()

    explicit_knobs = any(
        getattr(args, k["key"], None) is not None for k in meta["knobs"]
    )
    preset = args.costs or meta["default_preset"]

    def values_from(defaults: dict, preset: str) -> dict[str, float]:
        values = {}
        for knob in _applicable_knobs(meta, preset):
            key = knob["key"]
            flag = getattr(args, key, None)
            values[key] = float(defaults[key] if flag is None else flag)
        return values

    def dates() -> tuple[str | None, str | None]:
        if not ask_period:
            return _resolve_dates(args)
        if args.from_date is not None or args.till_date is not None:
            return _resolve_dates(args)
        return _ask_period() if prompt else (None, None)

    if args.costs is not None or explicit_knobs or not prompt:
        from_date, till_date = _resolve_dates(args)
        cfg = _build(
            args, meta, preset, values_from(meta["defaults"], preset),
            from_date, till_date,
        )
        return cfg

    while True:
        preset = _ask_preset(meta, meta["default_preset"])
        if preset == "none":
            values = {}
            break
        values = {}
        for knob in _applicable_knobs(meta, preset):
            key = knob["key"]
            flag = getattr(args, key, None)
            if flag is not None:
                values[key] = float(flag)
                continue
            label, unit = _PROMPT.get(key, (knob["label"], knob["unit"]))
            shown = f"{label} ({unit})" if unit else label
            values[key] = _ask_float(
                shown, float(meta["defaults"][key]), float(knob["min"])
            )
        cfg = _build(args, meta, preset, values)
        _summary(meta, cfg)
        if _ask_yes_no("Use these costs?"):
            break
        print("restarting the cost wizard...")
    from_date, till_date = dates()
    return _build(args, meta, preset, values, from_date, till_date)
