"""Download Nifty50 (^NSEI) spot 30-minute bars into nifty50_aug_oct.csv.

Fetches 15-minute bars from Yahoo and pairs them into session-aligned
30-minute bars stamped by start time: 0915 (09:15-09:45), 0945, ...,
1515 (15:15-15:30) — matching NSE market hours.

Same layout as new_data.csv: header "Date,Time,Open,High,Low,Close"
(Date DD/MM/YYYY, Time HHMM 24-hour) plus a trailing volume column on
each row that is not named in the header.

Yahoo only keeps ~60 days of intraday history, so START is as far back
as the API allows within the Aug-Oct 2026 window.

Usage:
    python fetch_nifty50.py
"""

import sys
from datetime import timedelta
from pathlib import Path

import yfinance as yf

OUT = Path(__file__).with_name("nifty50_aug_oct.csv")
START = "2026-08-21"  # earliest intraday data Yahoo still serves
END = "2026-10-19"  # exclusive


def fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def main():
    df = yf.download(
        "^NSEI", start=START, end=END, interval="15m", auto_adjust=False
    )
    if df.empty:
        print("No data returned", file=sys.stderr)
        raise SystemExit(1)
    df = df.tz_convert("Asia/Kolkata")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    # Pair 15m bars into 30m bars starting at :15/:45.
    df["bar"] = [
        t if t.minute % 30 == 15 else t - timedelta(minutes=15)
        for t in df.index
    ]
    rows = [
        (
            bar,
            float(g["Open"].iloc[0]),
            float(g["High"].max()),
            float(g["Low"].min()),
            float(g["Close"].iloc[-1]),
            int(g["Volume"].sum()),
        )
        for bar, g in df.groupby("bar")
    ]
    rows.sort(key=lambda r: r[0])

    with OUT.open("w") as f:
        f.write("Date,Time,Open,High,Low,Close\n")
        for t, o, h, l, c, v in rows:
            f.write(
                f"{t.strftime('%d/%m/%Y')},{t.strftime('%H%M')},"
                f"{fmt(o)},{fmt(h)},{fmt(l)},{fmt(c)},{v}\n"
            )
    print(
        f"wrote {len(rows)} bars, "
        f"{rows[0][0]:%d/%m/%Y %H%M} -> {rows[-1][0]:%d/%m/%Y %H%M}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
