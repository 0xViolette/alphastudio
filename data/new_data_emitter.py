"""Emit new_data.csv as raw little-endian OHLC binary to stdout.

Date and Time are joined into a single timestamp and emitted as Unix
seconds; the naive wall clock is interpreted as IST (Asia/Kolkata), so
converting a timestamp back with Asia/Kolkata recovers the original
Date/Time. Assumes the CSV is already in chronological order
(per protocol/ohlc.md).

Differences from data.csv: Date is DD/MM/YYYY and Time is a 24-hour
HHMM field. The trailing volume column (present in the rows but not the
header) is ignored; the wire format is OHLC only.

Usage:
    python new_data_emitter.py > ohlc.bin
    python new_data_emitter.py | <consumer>

Direct runs in a terminal are refused; stdout must be redirected or piped.
"""

import csv
import os
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CSV = Path(__file__).with_name("new_data.csv")
STRUCT = struct.Struct("<Qdddd")
IST = timezone(timedelta(hours=5, minutes=30))


def main():
    # Restore default SIGPIPE disposition so a downstream consumer that
    # exits early (e.g. `| head -c 40`) kills us with exit 141 instead of
    # raising BrokenPipeError with a traceback. Guarded for Windows.
    try:
        from signal import SIG_DFL, SIGPIPE, signal

        signal(SIGPIPE, SIG_DFL)
    except (AttributeError, ImportError, OSError, ValueError):
        pass
    if sys.stdout.isatty():
        print(
            "Refusing to write binary OHLC to terminal; "
            "redirect to a file (> ohlc.bin) or pipe to a consumer (| ...).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    out = sys.stdout.buffer
    try:
        with CSV.open(newline="") as f:
            for row in csv.DictReader(f):
                stamp = datetime.strptime(
                    f"{row['Date']} {row['Time']}", "%d/%m/%Y %H%M"
                ).replace(tzinfo=IST)
                out.write(
                    STRUCT.pack(
                        int(stamp.timestamp()),
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"]),
                    )
                )
    except BrokenPipeError:
        # Downstream closed the pipe early. Silence the traceback and the
        # second "Exception ignored on flushing sys.stdout" error by
        # redirecting stdout to devnull before exiting.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
