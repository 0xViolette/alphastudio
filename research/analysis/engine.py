"""Run strategy executables and decode their bar-by-bar CSV output."""

import csv
import io
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

from config import ROOT

EMITTER = ROOT / "research" / "emitters" / "ohlc_csv_emitter.py"


def strategies() -> dict[str, str]:
    return {
        path.stem: f"research/strategies/{path.stem}.exe"
        for path in sorted((ROOT / "research" / "strategies").glob("*.ml"))
    }


STRATEGIES = strategies()
DEFAULT_STRATEGY = "main" if "main" in STRATEGIES else next(iter(STRATEGIES), "")

SIDE_BY_TAG = {
    "LE": +1,
    "SE": -1,
    "2NL": -1,
    "2NS": +1,
    "TRL": -1,
    "TRS": +1,
    "LE cover": +1,
    "SE cover": -1,
    "TP_L": -1,
    "SL_L": -1,
    "TS_L": -1,
    "TP_S": +1,
    "SL_S": +1,
    "TS_S": +1,
}


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
class BacktestStream:
    bars: list[Bar]
    probe_names: list[str]
    tags: dict[str, int]


def run_backtest(dune_target, cost_argv=None, *, emitter=EMITTER):
    emitter_process = subprocess.Popen(
        [sys.executable, str(emitter)], stdout=subprocess.PIPE, cwd=ROOT
    )
    cmd = ["dune", "exec", dune_target, "--"] + list(cost_argv or [])
    strategy = subprocess.Popen(
        cmd,
        stdin=emitter_process.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    emitter_process.stdout.close()
    out, err = strategy.communicate()
    emitter_process.wait()
    if strategy.returncode != 0:
        sys.exit(f"dune exec strategy failed ({strategy.returncode}):\n{err}")
    if emitter_process.returncode != 0:
        sys.exit(f"data emitter failed ({emitter_process.returncode}): {emitter}")
    return out, err


def parse_tags(out):
    tags = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("# tags:"):
            continue
        for token in line[len("# tags:"):].split():
            if "=" in token:
                name, side = token.rsplit("=", 1)
                tags[name] = int(side)
    return tags


def _split_fill_column(value):
    return value.split("|") if value else []


def _reported_side(value):
    side = int(value)
    if side not in (-1, 1):
        raise ValueError(f"invalid fill side {value!r}; expected +1 or -1")
    return side


def parse_stream(text, *, side_for=None):
    """Decode a strategy CSV stream into bars, probes, and tagged fills.

    Explicit fill columns are authoritative. Manifests and `side_for` are
    fallbacks for legacy streams that only identify fills in the event column.
    """
    tags = parse_tags(text)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    if not rows:
        return BacktestStream([], [], tags)

    header = None
    for i, row in enumerate(rows):
        if row and "Date" in row and "event" in row:
            header = row
            rows = rows[i:]
            break
    if header is None:
        return BacktestStream([], [], tags)

    i_dt, i_tm = header.index("Date"), header.index("Time")
    i_o, i_h, i_l, i_c = (
        header.index(column) for column in ("Open", "High", "Low", "Close")
    )
    i_event = header.index("event")
    i_id = header.index("fill_id") if "fill_id" in header else None
    i_side = header.index("fill_side") if "fill_side" in header else None
    if (i_id is None) != (i_side is None):
        raise ValueError("fill_id and fill_side must appear together")
    i_price, i_qty = header.index("fill_price"), header.index("fill_qty")
    i_cost = header.index("fill_cost") if "fill_cost" in header else None
    probe_names = header[i_c + 1:i_event]

    def legacy_fill_side(tag):
        if side_for is not None:
            return side_for(tag)
        if tag in tags:
            return tags[tag]
        if tag in SIDE_BY_TAG:
            return SIDE_BY_TAG[tag]
        raise ValueError(
            f"unknown order tag {tag!r}; add it to the strategy manifest or SIDE_BY_TAG"
        )

    bars = []
    for row in rows[1:]:
        if not row:
            continue
        dt = datetime.strptime(
            row[i_dt] + " " + row[i_tm], "%m/%d/%y %I:%M %p"
        )
        if i_id is not None:
            fill_tags = [
                tag.strip() for tag in _split_fill_column(row[i_id])
            ]
            sides = [
                _reported_side(side)
                for side in _split_fill_column(row[i_side])
            ]
        else:
            events = [
                event.strip()
                for event in row[i_event].split("|")
                if event.strip().endswith("filled")
            ]
            fill_tags = [event[:-len("filled")].strip() for event in events]
            sides = [legacy_fill_side(tag) for tag in fill_tags]
        prices = _split_fill_column(row[i_price])
        quantities = _split_fill_column(row[i_qty])
        costs = (
            _split_fill_column(row[i_cost])
            if i_cost is not None
            else ["0"] * len(fill_tags)
        )
        if not (
            len(fill_tags)
            == len(sides)
            == len(prices)
            == len(quantities)
            == len(costs)
        ):
            raise ValueError(
                f"event/fill columns disagree on row {row[0:2]}: "
                f"{row[i_event:i_cost if i_cost is not None else i_qty + 1]}"
            )
        fills = [
            Fill(side, float(price), float(qty), float(cost or 0), tag)
            for tag, side, price, qty, cost in zip(
                fill_tags, sides, prices, quantities, costs
            )
        ]
        probes = {
            name: float(row[i_c + 1 + j]) if row[i_c + 1 + j] else float("nan")
            for j, name in enumerate(probe_names)
        }
        bars.append(
            Bar(
                dt=dt,
                open=float(row[i_o]),
                high=float(row[i_h]),
                low=float(row[i_l]),
                close=float(row[i_c]),
                fills=tuple(fills),
                probes=probes,
            )
        )
    return BacktestStream(bars, probe_names, tags)
