"""Emit option-derived synthetic futures OHLC (30-min bars) as binary.

Synthetic futures price per synchronized 1-min bar = same-strike call close -
put close + strike (put-call parity). Thirty-minute bars use the first, maximum,
minimum, and last of those one-minute prices and are emitted as little-endian
OHLC, identical wire format to new_data_emitter.py.

Usage:
    python option_derived_emitter.py | <consumer>
"""
import csv
import struct
import sys
from datetime import datetime, time, timedelta, timezone
from collections import defaultdict

CSV = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "raw" / "nifty_atm_month1.csv"
STRUCT = struct.Struct("<Qdddd")
IST = timezone(timedelta(hours=5, minutes=30))
REGULAR_CLOSE = time(15, 30)


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
    legs = defaultdict(dict)  # (date,time) -> {CALL:{...}, PUT:{...}}
    with CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            key = (row["Date"].strip(), row["Time"].strip())
            leg = row["option_type"].strip().upper()
            entry = legs[key]
            entry[leg] = {
                "close": float(row["close"]),
                "strike": float(row["strike"]),
            }

    # Build synchronized 1-min synthetic prices.
    one_min = []  # list of (datetime, price)
    for (d, t), v in legs.items():
        if "CALL" not in v or "PUT" not in v:
            continue
        c, p = v["CALL"], v["PUT"]
        if c["strike"] != p["strike"]:
            continue
        strike = c["strike"]
        dt = datetime.strptime(d + t, "%d/%m/%Y%H%M").replace(tzinfo=IST)
        one_min.append((dt, c["close"] - p["close"] + strike))

    one_min.sort(key=lambda x: x[0])

    sessions = []
    session = []
    for item in one_min:
        dt = item[0]
        if session and (
            dt.date() != session[-1][0].date()
            or dt - session[-1][0] > timedelta(minutes=1)
        ):
            sessions.append(session)
            session = []
        session.append(item)
    if session:
        sessions.append(session)

    # Aggregate into 30-min bars, labeled by their end timestamp.
    bars = defaultdict(lambda: {"o": None, "h": None, "l": None, "c": None, "n": 0})
    dropped_post_close = 0
    dropped_sessions = 0
    for session in sessions:
        evening = session[0][0].time() > REGULAR_CLOSE
        if not evening:
            regular = [item for item in session if item[0].time() <= REGULAR_CLOSE]
            dropped = len(session) - len(regular)
            if dropped:
                dropped_post_close += dropped
                dropped_sessions += 1
            session = regular
        if not session:
            continue

        session_end = session[-1][0]
        for dt, price in session:
            minute = (dt.minute // 30) * 30
            wstart = dt.replace(minute=minute, second=0, microsecond=0)
            wend = wstart + timedelta(minutes=30)
            if dt == session_end and dt == wstart:
                if dt.time() == REGULAR_CLOSE or wstart in bars:
                    wend = wstart
            b = bars[wend]
            if b["n"] == 0:
                b["o"] = price
                b["h"] = price
                b["l"] = price
            else:
                b["h"] = max(b["h"], price)
                b["l"] = min(b["l"], price)
            b["c"] = price
            b["n"] += 1

    if dropped_post_close:
        print(
            f"Warning: dropped {dropped_post_close} post-close one-minute rows "
            f"after 15:30 across {dropped_sessions} sessions",
            file=sys.stderr,
        )

    out = sys.stdout.buffer
    for wend in sorted(bars):
        b = bars[wend]
        if b["n"] == 0 or b["o"] is None:
            continue
        out.write(STRUCT.pack(int(wend.timestamp()), b["o"], b["h"], b["l"], b["c"]))

    try:
        out.flush()
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
