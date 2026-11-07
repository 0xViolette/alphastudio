"""Run OCaml strategies and decode their current CSV output."""

from __future__ import annotations

import csv
import io
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DIR = ROOT / "research" / "strategies"

REQUIRED_COLUMNS = (
    "Date",
    "Time",
    "Open",
    "High",
    "Low",
    "Close",
    "event",
    "fill_id",
    "fill_side",
    "fill_price",
    "fill_qty",
    "fill_fee",
)


class BacktestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Fill:
    side: int
    price: float
    qty: float
    fee: float
    tag: str = ""


@dataclass(frozen=True, slots=True)
class Bar:
    dt: datetime
    close: float
    fills: tuple[Fill, ...] = ()
    open: float | None = None
    high: float | None = None
    low: float | None = None
    probes: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BacktestResult:
    bars: list[Bar]
    probe_names: list[str]


def available_strategies() -> list[str]:
    return [path.stem for path in sorted(STRATEGY_DIR.glob("*.ml"))]


def _fill_values(row: dict[str, str], column: str) -> list[str]:
    value = row.get(column, "")
    return value.split("|") if value else []


def parse_stream(text: str) -> BacktestResult:
    """Parse one fresh backtest; obsolete stream formats are intentionally rejected."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise BacktestError("strategy output has no CSV header")
    missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
    if missing:
        raise BacktestError(
            "strategy output does not use the current schema; missing: "
            + ", ".join(missing)
        )

    probe_names = reader.fieldnames[
        reader.fieldnames.index("Close") + 1 : reader.fieldnames.index("event")
    ]
    bars = []
    for line_number, row in enumerate(reader, start=2):
        if not any(row.values()):
            continue
        try:
            dt = datetime.strptime(
                f"{row['Date']} {row['Time']}", "%m/%d/%y %I:%M %p"
            )
            ids = [tag.strip() for tag in _fill_values(row, "fill_id")]
            sides = [int(value) for value in _fill_values(row, "fill_side")]
            prices = [float(value) for value in _fill_values(row, "fill_price")]
            quantities = [float(value) for value in _fill_values(row, "fill_qty")]
            fees = [float(value) for value in _fill_values(row, "fill_fee")]
            if not all(side in (-1, 1) for side in sides):
                raise ValueError("fill_side must contain only +1 or -1")
            lengths = {len(ids), len(sides), len(prices), len(quantities), len(fees)}
            if len(lengths) > 1:
                raise ValueError("fill columns contain different numbers of values")
            fills = tuple(
                Fill(side, price, qty, fee, tag)
                for tag, side, price, qty, fee in zip(
                    ids, sides, prices, quantities, fees
                )
            )
            probes = {
                name: float(row[name]) if row[name] else math.nan
                for name in probe_names
            }
            bars.append(
                Bar(
                    dt=dt,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    fills=fills,
                    probes=probes,
                )
            )
        except (TypeError, ValueError) as exc:
            raise BacktestError(f"invalid strategy CSV row {line_number}: {exc}") from exc
    return BacktestResult(bars, probe_names)


def run_backtest(
    strategy: str,
    cost_args: Sequence[str],
    *,
    emitter: Path,
    parse_output: bool = True,
) -> BacktestResult | None:
    if strategy not in available_strategies():
        raise BacktestError(f"unknown strategy {strategy!r}")

    emitter_process = subprocess.Popen(
        [sys.executable, str(emitter)], stdout=subprocess.PIPE, cwd=ROOT
    )
    command = [
        "dune",
        "exec",
        f"research/strategies/{strategy}.exe",
        "--",
        *cost_args,
    ]
    strategy_process = subprocess.Popen(
        command,
        stdin=emitter_process.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    emitter_process.stdout.close()
    output, errors = strategy_process.communicate()
    emitter_status = emitter_process.wait()

    if strategy_process.returncode:
        raise BacktestError(
            f"strategy exited {strategy_process.returncode}:\n{errors.strip()}"
        )
    if emitter_status not in (0, -13, 141):
        raise BacktestError(f"data emitter exited {emitter_status}: {emitter}")
    if not parse_output:
        return None
    result = parse_stream(output)
    if not result.bars:
        raise BacktestError(f"strategy produced no bars; stderr was:\n{errors.strip()}")
    return result
