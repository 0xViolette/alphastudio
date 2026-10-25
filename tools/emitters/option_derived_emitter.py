"""Emit option-derived synthetic futures OHLC (30-min bars) as binary.

Synthetic futures price per 1-min bar = ATM call close - ATM put close + strike
(put-call parity). OHLC for each 1-min bar is derived from the legs:
  open  = call_open  - put_open  + strike
  high  = call_high  - put_low   + strike
  low   = call_low   - put_high  + strike
  close = call_close - put_close + strike
1-min bars are then aggregated into 30-min bars (open=first, high=max,
low=min, close=last) and emitted as little-endian OHLC, identical wire
format to new_data_emitter.py.

Usage:
    python option_derived_emitter.py | <consumer>
"""
import csv
import os
import struct
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

CSV = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "nifty_atm_month1.csv"
STRUCT = struct.Struct("<Qdddd")
IST = timezone(timedelta(hours=5, minutes=30))


def main():
    try:
        from signal import SIG_DFL, SIGPIPE, signal

        signal(SIGPIPE, SIG_DFL)
    except (AttributeError, ImportError, OSError, ValueError):
        pass
    if sys.stdout.isatty():
        print("Refusing to write binary OHLC to terminal", file=sys.stderr)
        raise SystemExit(2)

    # Read 1-min legs
    legs = defaultdict(dict)  # (date,time) -> {CALL:{...}, PUT:{...}, strike}
    with CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            key = (row["Date"].strip(), row["Time"].strip())
            leg = row["option_type"].strip().upper()
            entry = legs[key]
            entry[leg] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            entry["strike"] = float(row["strike"])

    # Build 1-min synthetic OHLC
    one_min = []  # list of (datetime, o, h, l, c)
    for (d, t), v in legs.items():
        if "CALL" not in v or "PUT" not in v:
            continue
        c, p = v["CALL"], v["PUT"]
        strike = v["strike"]
        dt = datetime.strptime(d + t, "%d/%m/%Y%H%M").replace(tzinfo=IST)
        one_min.append((
            dt,
            c["open"] - p["open"] + strike,
            c["high"] - p["low"] + strike,
            c["low"] - p["high"] + strike,
            c["close"] - p["close"] + strike,
        ))

    one_min.sort(key=lambda x: x[0])

    # Aggregate into 30-min bars
    bars = defaultdict(lambda: {"o": None, "h": None, "l": None, "c": None, "n": 0})
    for dt, o, h, l, c in one_min:
        # floor to 30-min
        minute = (dt.minute // 30) * 30
        wstart = dt.replace(minute=minute, second=0, microsecond=0)
        b = bars[wstart]
        if b["n"] == 0:
            b["o"] = o
            b["h"] = h
            b["l"] = l
        else:
            b["h"] = max(b["h"], h)
            b["l"] = min(b["l"], l)
        b["c"] = c
        b["n"] += 1

    out = sys.stdout.buffer
    for wstart in sorted(bars):
        b = bars[wstart]
        if b["n"] == 0 or b["o"] is None:
            continue
        out.write(STRUCT.pack(int(wstart.timestamp()), b["o"], b["h"], b["l"], b["c"]))

    try:
        out.flush()
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
