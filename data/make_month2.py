#!/usr/bin/env python3
"""Export month2 (next monthly, expiry_code=2) ATM option bars to
local_data/nifty_atm_month2.csv, mirroring nifty_atm_month1.csv."""
import datetime, csv
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "dhan" / "data" / "pack"
OUT = ROOT / "data" / "nifty_atm_month2.csv"

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Load 2C (call) and 2P (put) ATM packs.
def load_atm(prefix):
    ts = np.load(PACK / f"{prefix}_atm_ts.npy")
    strike = np.load(PACK / f"{prefix}_atm_strike.npy")
    spot = np.load(PACK / f"{prefix}_atm_spot.npy")
    o = np.load(PACK / f"{prefix}_atm_open.npy")
    h = np.load(PACK / f"{prefix}_atm_high.npy")
    l = np.load(PACK / f"{prefix}_atm_low.npy")
    c = np.load(PACK / f"{prefix}_atm_close.npy")
    return ts, strike, spot, o, h, l, c

ts_c, strike_c, spot_c, o_c, h_c, l_c, c_c = load_atm("2C")
ts_p, strike_p, spot_p, o_p, h_p, l_p, c_p = load_atm("2P")

# Index puts by timestamp for alignment.
put_by_ts = {}
for i in range(len(ts_p)):
    put_by_ts[int(ts_p[i])] = (float(strike_p[i]), float(spot_p[i]),
                               float(o_p[i]), float(h_p[i]), float(l_p[i]), float(c_p[i]))

rows_written = 0
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Time", "strike", "option_type", "spot", "open", "high", "low", "close"])
    for i in range(len(ts_c)):
        t = int(ts_c[i])
        p = put_by_ts.get(t)
        if p is None:
            continue
        dt = datetime.datetime.fromtimestamp(t, tz=IST)
        date_s = dt.strftime("%d/%m/%Y")
        time_s = dt.strftime("%H%M")
        # CALL row (from 2C)
        w.writerow([date_s, time_s, float(strike_c[i]), "CALL",
                    float(spot_c[i]), float(o_c[i]), float(h_c[i]), float(l_c[i]), float(c_c[i])])
        # PUT row (from 2P)
        ps, pspot, po, ph, pl, pc = p
        w.writerow([date_s, time_s, ps, "PUT",
                    pspot, po, ph, pl, pc])
        rows_written += 2

print(f"wrote {rows_written} rows ({rows_written//2} minutes) to {OUT}")
