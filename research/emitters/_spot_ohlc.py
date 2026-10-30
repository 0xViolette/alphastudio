"""Aggregate regular-session NIFTY spot minutes into 30-minute OHLC bars."""

import csv
import os
import struct
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

CSV = Path(__file__).resolve().parents[2] / "data" / "raw" / "nifty50_1min.csv"
STRUCT = struct.Struct("<Qdddd")
REGULAR_OPEN = time(9, 15)
REGULAR_CLOSE = time(15, 30)


def aggregate(minutes, *, close_only):
    sessions = []
    for item in minutes:
        if not sessions or (
            item[0].date() != sessions[-1][-1][0].date()
            or item[0] - sessions[-1][-1][0] > timedelta(minutes=1)
        ):
            sessions.append([])
        sessions[-1].append(item)

    grouped = defaultdict(list)
    corrections = 0
    for session in sessions:
        session_end = session[-1][0]
        for dt, open_, high, low, close in session:
            if close_only:
                open_ = high = low = close
            else:
                fixed_high = max(high, open_, close)
                fixed_low = min(low, open_, close)
                corrections += fixed_high != high or fixed_low != low
                high, low = fixed_high, fixed_low
            wstart = dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)
            wend = wstart + timedelta(minutes=30)
            if dt == session_end and dt == wstart:
                if dt.time() == REGULAR_CLOSE or wstart in grouped:
                    wend = wstart
            grouped[wend].append((open_, high, low, close))

    bars = {
        dt: (
            values[0][0],
            max(value[1] for value in values),
            min(value[2] for value in values),
            values[-1][3],
        )
        for dt, values in grouped.items()
    }
    return bars, corrections


def load_bars(*, close_only):
    minutes = []
    with CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            dt = datetime.fromisoformat(row["datetime"])
            if not REGULAR_OPEN <= dt.time() <= REGULAR_CLOSE:
                continue
            minutes.append((
                dt,
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            ))
    return aggregate(minutes, close_only=close_only)


def main(*, close_only):
    if sys.stdout.isatty():
        print("Refusing to write binary OHLC to terminal", file=sys.stderr)
        raise SystemExit(2)
    bars, corrections = load_bars(close_only=close_only)
    if corrections:
        print(
            f"Warning: normalized inconsistent high/low envelopes on {corrections} minutes",
            file=sys.stderr,
        )
    try:
        for dt in sorted(bars):
            open_, high, low, close = bars[dt]
            sys.stdout.buffer.write(
                STRUCT.pack(int(dt.timestamp()), open_, high, low, close)
            )
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
