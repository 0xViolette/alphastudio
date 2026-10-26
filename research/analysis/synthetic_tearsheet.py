#!/usr/bin/env python3
"""Real-futures backtesting with options expiry rollover and cost wizard.

Implements strategy execution on real NIFTY futures (new_data.csv) with:
- Expiry detection using NSE monthly expiry rules (last Thursday before Sep 2025,
  last Tuesday from Sep 2025 onward), holiday-adjusted against trading calendar
- On expiry day at 15:20, square off any open position
- Re-enter the same position at 15:21 with the next month's expiry contract
- Full cost wizard (like tearsheet2.py) with option costs (brokerage, txn, SEBI,
  STT on sell leg, stamp on buy leg, GST) from OCaml schema; premiums from data

Usage (from strategy/ project root):
    python3 scripts/synthetic_tearsheet.py [STRATEGY]
        [--rf 0.065] [--mar 0.0] [--trials 100] [--export [report.html]]
        [--costs none|futures|synthetic_futures|simple]
        [--from YYYY-MM-DD] [--till YYYY-MM-DD]
"""

import argparse
import bisect
import calendar
import csv
import os
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite, sqrt
from pathlib import Path
from statistics import NormalDist

import costs as cost_cli
import tearsheet as ts
from plot_pnl import (
    DEFAULT_STRATEGY,
    EMITTER,
    SIDE_BY_TAG,
    STRATEGIES,
    is_zero,
    resolve_strategy,
    run_backtest,
    sgn,
)

# Filter cost presets: only "none" and "synthetic_futures" are relevant for
# synthetic futures (premiums come from real option data, not estimated).
_original_schema = cost_cli.schema
def _filtered_schema():
    meta = _original_schema()
    meta = dict(meta)
    # Rename "synthetic_futures" -> "costs" for cleaner UX
    filtered = []
    for p in meta["presets"]:
        if p["name"] == "none":
            filtered.append(p)
        elif p["name"] == "synthetic_futures":
            p = dict(p)
            p["name"] = "costs"
            p["description"] = "option costs (brokerage, txn, SEBI, STT, stamp, GST)"
            filtered.append(p)
    meta["presets"] = filtered
    meta["default_preset"] = "costs"
    for k in meta["knobs"]:
        if "synthetic_futures" in k.get("applies_to", []):
            k["applies_to"] = ["costs" if x == "synthetic_futures" else x
                               for x in k["applies_to"]]
    meta["knobs"] = [k for k in meta["knobs"]
                     if k["key"] in ("fee_per_order", "opt_slip_ticks", "opt_slip_pct")
                     and any(p in k["applies_to"] for p in ("none", "costs"))]
    return meta
cost_cli.schema = _filtered_schema

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PNG = ROOT / "reports" / "plots" / "synthetic_tearsheet.png"
NIFTY_CSV = ROOT / "data" / "raw" / "nifty_atm_month1.csv"
NIFTY_MONTH2_CSV = ROOT / "data" / "raw" / "nifty_atm_month2.csv"
DATA_CSV = ROOT / "data" / "raw" / "new_data.csv"

W = 84
NORMAL = NormalDist()
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

# Expiry rules (NSE: last Thursday until 2025-08-31, then last Tuesday)
EXPIRY_RULES = (
    (date(2000, 1, 1), 3),   # last Thursday
    (date(2025, 9, 1), 1),   # last Tuesday
)

# Square off the expiring contract at 15:20 and switch at 15:21.
ROLLOVER_CUTOFF = time(15, 20)

BUY, SELL = +1, -1


tone = ts.tone
dim = ts.dim
bold = ts.bold
rule = ts.rule
kv = ts.kv
banner = ts.banner


def _weekday_rule(day: date) -> int:
    rule = EXPIRY_RULES[0][1]
    for effective, weekday in EXPIRY_RULES:
        if day >= effective:
            rule = weekday
    return rule


def nominal_monthly_expiry(year: int, month: int, at: date) -> date:
    weekday = _weekday_rule(at)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def build_expiry_calendar(trading_dates: set, start: date, end: date):
    """Monthly expiries, adjusted only when later sessions prove a closure."""
    expiries = []
    first_month = date(start.year, start.month, 1)
    last_month = date(end.year, end.month, 1)
    for _ in range(3):
        last_month = date(last_month.year + (last_month.month == 12),
                          last_month.month % 12 + 1, 1)
    cursor = first_month
    while cursor <= last_month:
        nominal = nominal_monthly_expiry(cursor.year, cursor.month, at=cursor)
        adjusted = nominal
        if nominal not in trading_dates and any(
                nominal < d <= nominal + timedelta(days=7) for d in trading_dates):
            candidate = nominal - timedelta(days=1)
            earliest = max(date(cursor.year, cursor.month, 1),
                           nominal - timedelta(days=7))
            while candidate >= earliest and candidate not in trading_dates:
                candidate -= timedelta(days=1)
            if candidate >= earliest:
                adjusted = candidate
        expiries.append(adjusted)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return sorted(set(expiries))


class OptionCostModel:
    """Per-leg option execution costs from OCaml schema rates."""

    def __init__(self, meta, values=None):
        rates = meta["rates"]
        defaults = meta["defaults"]
        values = values or {}
        self.nse_options_txn_bps = float(rates["nse_options_txn_bps"])
        self.sebi_bps = float(rates["sebi_bps"])
        self.stt_options_bps = float(rates["stt_options_bps"])
        self.stamp_options_bps = float(rates["stamp_options_bps"])
        self.gst = float(rates["gst"])
        self.fee_per_order = float(values.get("fee_per_order", defaults["fee_per_order"]))
        self.opt_slip_ticks = float(values.get("opt_slip_ticks", defaults["opt_slip_ticks"]))
        self.opt_slip_pct = float(values.get("opt_slip_pct", defaults["opt_slip_pct"]))
        self.opt_tick_size = float(defaults["opt_tick_size"])
        self.slippage_multiplier = 1.0

    def slippage(self, premium):
        return max(0.0, self.slippage_multiplier * (
            self.opt_slip_ticks * self.opt_tick_size
            + self.opt_slip_pct / 100.0 * premium
        ))

    def execution_price(self, side, open_price):
        return max(self.opt_tick_size,
                   open_price + side * self.slippage(open_price))

    def fee(self, side, exec_price, lots, lot_size):
        """Total nonnegative charge for one executed leg."""
        return sum(self._leg_breakup(side, exec_price, lots, lot_size).values())

    def futures_fee(self, side, call_px, put_px, lots, lot_size):
        """Total option cost for a synthetic futures fill."""
        if side > 0:
            return (self.fee(BUY, call_px, lots, lot_size) +
                    self.fee(SELL, put_px, lots, lot_size))
        return (self.fee(SELL, call_px, lots, lot_size) +
                self.fee(BUY, put_px, lots, lot_size))

    def futures_cost(self, side, call_open, put_open, lots, lot_size):
        """Execute both option legs and return actual slippage and costs."""
        call_side = BUY if side > 0 else SELL
        put_side = -call_side
        call_px = self.execution_price(call_side, call_open)
        put_px = self.execution_price(put_side, put_open)
        slip_call = call_side * (call_px - call_open)
        slip_put = put_side * (put_px - put_open)
        fee = self.futures_fee(side, call_px, put_px, lots, lot_size)
        breakup = self.futures_fee_breakup(
            side, call_px, put_px, lots, lot_size,
            raw_call_px=call_open, raw_put_px=put_open)
        return {
            "call_price": call_px,
            "put_price": put_px,
            "slippage": slip_call + slip_put,
            "fee": fee,
            "breakup": breakup,
        }

    def futures_fee_breakup(self, side, call_px, put_px, lots, lot_size,
                            raw_call_px=None, raw_put_px=None):
        """Return cost breakup dict for a synthetic futures fill."""
        call_side = BUY if side > 0 else SELL
        put_side = -call_side
        leg1 = self._leg_breakup(call_side, call_px, lots, lot_size)
        leg2 = self._leg_breakup(put_side, put_px, lots, lot_size)

        def raw_price(leg_side, exec_price):
            fixed = self.slippage_multiplier * self.opt_slip_ticks * self.opt_tick_size
            pct = self.slippage_multiplier * self.opt_slip_pct / 100.0
            return (exec_price - leg_side * fixed) / (1 + leg_side * pct)

        if raw_call_px is None:
            raw_call_px = raw_price(call_side, call_px)
        if raw_put_px is None:
            raw_put_px = raw_price(put_side, put_px)
        return {
            "brokerage": leg1["brokerage"] + leg2["brokerage"],
            "txn": leg1["txn"] + leg2["txn"],
            "sebi": leg1["sebi"] + leg2["sebi"],
            "stt": leg1["stt"] + leg2["stt"],
            "stamp": leg1["stamp"] + leg2["stamp"],
            "gst": leg1["gst"] + leg2["gst"],
            "slippage_call": call_side * (call_px - raw_call_px),
            "slippage_put": put_side * (put_px - raw_put_px),
        }

    def _leg_breakup(self, side, exec_price, lots, lot_size):
        turnover = max(0.0, exec_price * lots * lot_size)
        brokerage = max(0.0, self.fee_per_order)
        txn = max(0.0, self.nse_options_txn_bps * 1e-4 * turnover)
        sebi = max(0.0, self.sebi_bps * 1e-4 * turnover)
        stt = (max(0.0, self.stt_options_bps * 1e-4 * turnover)
               if side == SELL else 0.0)
        stamp = (max(0.0, self.stamp_options_bps * 1e-4 * turnover)
                 if side == BUY else 0.0)
        gst = max(0.0, self.gst * (brokerage + txn + sebi))
        return {
            "brokerage": brokerage,
            "txn": txn,
            "sebi": sebi,
            "stt": stt,
            "stamp": stamp,
            "gst": gst,
        }


def load_option_net(csv_path: Path):
    """Read nifty_atm_month1.csv -> (times, nets, strikes, calls, puts).

    net premium = ATM call close - ATM put close for each one-minute bar.
    By put-call parity, net premium + strike approximates the futures level.
    Only minutes carrying both legs are tradeable.
    Also returns the raw call and put close prices for cost calculation.
    """
    legs = {}
    with csv_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return [], [], [], [], []
        i_date, i_time = header.index("Date"), header.index("Time")
        i_strike = header.index("strike")
        i_type = header.index("option_type")
        i_close = header.index("close")
        for row in reader:
            if not row or len(row) <= i_close:
                continue
            dt = datetime.strptime(row[i_date].strip() + row[i_time].strip(),
                                   "%d/%m/%Y%H%M")
            leg = row[i_type].strip().upper()
            entry = legs.setdefault(dt, {})
            entry[leg] = float(row[i_close])
            entry[f"{leg}_strike"] = float(row[i_strike])

    times, nets, strikes, calls, puts = [], [], [], [], []
    for dt in sorted(legs):
        v = legs[dt]
        if ("CALL" in v and "PUT" in v
                and v["CALL_strike"] == v["PUT_strike"]):
            times.append(dt)
            nets.append(v["CALL"] - v["PUT"])
            strikes.append(v["CALL_strike"])
            calls.append(v["CALL"])
            puts.append(v["PUT"])
    return times, nets, strikes, calls, puts


def make_option_lookup(times, nets, strikes, calls=None, puts=None):
    """Return the latest same-session option quote at or before t."""
    if not times:
        return lambda t: None
    calls = calls or [0.0] * len(times)
    puts = puts or [0.0] * len(times)

    def lookup(t):
        i = bisect.bisect_right(times, t) - 1
        if i < 0 or times[i].date() != t.date():
            return None
        return nets[i], strikes[i], calls[i], puts[i]

    return lookup

def load_next_month_from_csv(csv_path):
    """Load next-month (month2) ATM option data from a CSV with the same schema
    as nifty_atm_month1.csv. Returns (unix timestamps, nets, strikes, calls,
    puts)."""
    import datetime
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    times_dt, nets, strikes, calls, puts = load_option_net(csv_path)
    if not times_dt:
        return [], [], [], [], []
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    times = [int(dt.replace(tzinfo=ist).timestamp()) for dt in times_dt]
    return times, nets, strikes, calls, puts


def make_next_month_lookup(times, nets, strikes, calls=None, puts=None):
    """Return the latest same-session next-month quote at or before t."""
    if not times:
        return lambda t: None
    calls = calls or [0.0] * len(times)
    puts = puts or [0.0] * len(times)

    def lookup(t):
        if isinstance(t, datetime):
            ts = int(t.replace(tzinfo=__import__("datetime").timezone(
                __import__("datetime").timedelta(hours=5, minutes=30))).timestamp())
        else:
            ts = int(t)
        i = bisect.bisect_right(times, ts) - 1
        if i < 0 or (times[i] + 19800) // 86400 != (ts + 19800) // 86400:
            return None
        return nets[i], strikes[i], calls[i], puts[i]

    return lookup


IST = timezone(timedelta(hours=5, minutes=30))


def option_trading_dates(front_times, month2_times=()):
    """All unfiltered sessions covered by the loaded option contracts."""
    dates = {dt.date() for dt in front_times}
    dates.update(datetime.fromtimestamp(ts, IST).date() for ts in month2_times)
    return dates


def _require_option_leg(lookup, dt, contract):
    leg = lookup(dt) if lookup is not None else None
    if leg is None or len(leg) < 4:
        raise ValueError(f"no same-session {contract} option quote at {dt}")
    return leg


def _fill_cost(cost_model, side, leg, qty, lot_size):
    if cost_model is None:
        return 0.0, 0.0, {
            "brokerage": 0, "txn": 0, "sebi": 0, "stt": 0,
            "stamp": 0, "gst": 0, "slippage_call": 0, "slippage_put": 0,
            "call_premium": leg[2], "put_premium": leg[3],
        }
    cost = cost_model.futures_cost(side, leg[2], leg[3], qty, lot_size)
    breakup = cost["breakup"]
    breakup["call_premium"] = cost["call_price"]
    breakup["put_premium"] = cost["put_price"]
    return cost["slippage"], cost["fee"], breakup


def _math_prod(xs):
    out = 1.0
    for x in xs:
        out *= 1 + x
    return out


def synthetic_bars_with_rollover(bars, opt_net, first_dt, last_dt, expiries,
                                 cost_model, lot_size,
                                 next_month_lookup=None):
    """Convert futures bars to synthetic bars with expiry rollover.

    Square off at 15:20 and re-enter at 15:21 on expiry day. Later bars
    and fills use month2; every mark and execution needs a same-session quote.

    Returns (synthetic bars, futures closes, roll event list, trade log).
    """
    by_date = {}
    expiry_dates = set(expiries)
    for dt, futures_close, fills in bars:
        if first_dt <= dt <= last_dt:
            if dt.date() in expiry_dates and dt.time() == time(15, 30) and fills:
                print(f"warning: real-futures expiry fills at {dt} remain at "
                      "15:30; intrabar stop timing is not reconstructed",
                      file=sys.stderr)
            by_date.setdefault(dt.date(), []).append((dt, futures_close, fills))
    if by_date:
        first_day, last_day = min(by_date), max(by_date)
        for expiry in expiries:
            if first_day <= expiry <= last_day:
                by_date.setdefault(expiry, [])

    out, fut_closes, roll_events = [], [], []
    trade_log = []
    position = 0
    current_expiry_idx = None

    for bar_date in sorted(by_date):
        day_bars = sorted(by_date[bar_date], key=lambda bar: bar[0])

        # Determine current expiry if not yet set
        if current_expiry_idx is None:
            for i, exp in enumerate(expiries):
                if exp >= bar_date:
                    current_expiry_idx = i
                    break

        is_expiry_day = (
            current_expiry_idx is not None
            and bar_date == expiries[current_expiry_idx]
        )
        contract_flipped = False

        # Process regular bars for this day
        for dt, futures_close, fills in day_bars:
            # The first later bar triggers the 15:20/15:21 roll. Fills at
            # 15:20 stay in the old month; later fills use month2 directly.
            at_cutoff = (
                is_expiry_day
                and not contract_flipped
                and dt.time() > ROLLOVER_CUTOFF
            )
            if at_cutoff:
                contract_flipped = True
                rollover_dt1 = datetime(bar_date.year, bar_date.month, bar_date.day, 15, 20)
                rollover_dt2 = datetime(bar_date.year, bar_date.month, bar_date.day, 15, 21)
                leg1 = _require_option_leg(opt_net, rollover_dt1, "front-month")
                leg2 = _require_option_leg(
                    next_month_lookup, rollover_dt2, "month2 reentry")
                price1, price2 = leg1[0] + leg1[1], leg2[0] + leg2[1]
                if position == 0:
                    out.append((rollover_dt1, price1, []))
                    out.append((rollover_dt2, price2, []))
                    fut_closes.extend((price1, price2))
                    roll_events.append({
                        "date": rollover_dt2, "action": "contract_switch",
                        "qty": 0, "side": 0, "price": price2, "fee": 0.0,
                    })
                else:
                    qty = abs(position)
                    close_side = -1 if position > 0 else 1
                    slip1, fee1, breakup1 = _fill_cost(
                        cost_model, close_side, leg1, qty, lot_size)
                    fill_price1 = price1 + close_side * slip1
                    out.append((rollover_dt1, price1,
                                [(close_side, fill_price1, qty, fee1)]))
                    fut_closes.append(price1)
                    roll_events.append({
                        "date": rollover_dt1, "action": "square_off",
                        "qty": qty, "side": position,
                        "price": fill_price1, "fee": fee1,
                    })
                    trade_log.append({
                        "timestamp": rollover_dt1,
                        "action": "ROLLOVER_SQUAREOFF",
                        "futures_side": "SELL" if close_side < 0 else "BUY",
                        "option_legs": "SELL CE, BUY PE" if close_side < 0 else "BUY CE, SELL PE",
                        "qty": qty, "price": fill_price1, "fee": fee1,
                        "contract": "expiring", "is_rollover": True,
                        "cost_breakup": breakup1,
                    })
                    position = 0

                    reentry_side = -close_side
                    slip2, fee2, breakup2 = _fill_cost(
                        cost_model, reentry_side, leg2, qty, lot_size)
                    fill_price2 = price2 + reentry_side * slip2
                    out.append((rollover_dt2, price2,
                                [(reentry_side, fill_price2, qty, fee2)]))
                    fut_closes.append(price2)
                    roll_events.append({
                        "date": rollover_dt2, "action": "rollover",
                        "qty": qty, "side": reentry_side * qty,
                        "price": fill_price2, "fee": fee2,
                    })
                    trade_log.append({
                        "timestamp": rollover_dt2,
                        "action": "ROLLOVER_REENTRY",
                        "futures_side": "BUY" if reentry_side > 0 else "SELL",
                        "option_legs": "BUY CE, SELL PE" if reentry_side > 0 else "SELL CE, BUY PE",
                        "qty": qty, "price": fill_price2, "fee": fee2,
                        "contract": "next_month", "is_rollover": True,
                        "cost_breakup": breakup2,
                    })
                    position = reentry_side * qty
                current_expiry_idx += 1
                if current_expiry_idx >= len(expiries):
                    current_expiry_idx = None

            on_next_month = is_expiry_day and contract_flipped
            contract = "month2" if on_next_month else "front-month"
            leg = _require_option_leg(
                next_month_lookup if on_next_month else opt_net, dt, contract)
            synthetic_px = leg[0] + leg[1]
            sfills = []
            for side, raw_px, qty, _cost in fills:
                delta = side * qty
                closing = position != 0 and sgn(delta) == -sgn(position)
                slippage, fee, breakup = _fill_cost(
                    cost_model, side, leg, qty, lot_size)
                px = raw_px + synthetic_px - futures_close + side * slippage
                sfills.append((side, px, qty, fee))
                trade_log.append({
                    "timestamp": dt,
                    "action": "CLOSE" if closing else "OPEN",
                    "futures_side": "BUY" if side > 0 else "SELL",
                    "option_legs": "BUY CE, SELL PE" if side > 0 else "SELL CE, BUY PE",
                    "qty": qty,
                    "price": px,
                    "fee": fee,
                    "contract": "next_month" if on_next_month else "expiring",
                    "is_rollover": False,
                    "cost_breakup": breakup,
                })
                position += delta

            out.append((dt, synthetic_px, sfills))
            fut_closes.append(synthetic_px)

        if is_expiry_day and not contract_flipped:
            rollover_dt1 = datetime(bar_date.year, bar_date.month, bar_date.day, 15, 20)
            rollover_dt2 = datetime(bar_date.year, bar_date.month, bar_date.day, 15, 21)
            leg1 = _require_option_leg(opt_net, rollover_dt1, "front-month")
            leg2 = _require_option_leg(next_month_lookup, rollover_dt2, "month2 reentry")
            price1, price2 = leg1[0] + leg1[1], leg2[0] + leg2[1]
            if position == 0:
                out.append((rollover_dt1, price1, []))
                out.append((rollover_dt2, price2, []))
                fut_closes.extend((price1, price2))
                roll_events.append({
                    "date": rollover_dt2, "action": "contract_switch",
                    "qty": 0, "side": 0, "price": price2, "fee": 0.0,
                })
            else:
                qty = abs(position)
                close_side = -1 if position > 0 else 1
                slip1, fee1, breakup1 = _fill_cost(
                    cost_model, close_side, leg1, qty, lot_size)
                fill_price1 = price1 + close_side * slip1
                out.append((rollover_dt1, price1,
                            [(close_side, fill_price1, qty, fee1)]))
                fut_closes.append(price1)
                roll_events.append({
                    "date": rollover_dt1, "action": "square_off",
                    "qty": qty, "side": position, "price": fill_price1, "fee": fee1,
                })
                trade_log.append({
                    "timestamp": rollover_dt1, "action": "ROLLOVER_SQUAREOFF",
                    "futures_side": "SELL" if close_side < 0 else "BUY",
                    "option_legs": "SELL CE, BUY PE" if close_side < 0 else "BUY CE, SELL PE",
                    "qty": qty, "price": fill_price1, "fee": fee1,
                    "contract": "expiring", "is_rollover": True,
                    "cost_breakup": breakup1,
                })
                position = 0
                reentry_side = -close_side
                slip2, fee2, breakup2 = _fill_cost(
                    cost_model, reentry_side, leg2, qty, lot_size)
                fill_price2 = price2 + reentry_side * slip2
                out.append((rollover_dt2, price2,
                            [(reentry_side, fill_price2, qty, fee2)]))
                fut_closes.append(price2)
                roll_events.append({
                    "date": rollover_dt2, "action": "rollover",
                    "qty": qty, "side": reentry_side * qty,
                    "price": fill_price2, "fee": fee2,
                })
                trade_log.append({
                    "timestamp": rollover_dt2, "action": "ROLLOVER_REENTRY",
                    "futures_side": "BUY" if reentry_side > 0 else "SELL",
                    "option_legs": "BUY CE, SELL PE" if reentry_side > 0 else "SELL CE, BUY PE",
                    "qty": qty, "price": fill_price2, "fee": fee2,
                    "contract": "next_month", "is_rollover": True,
                    "cost_breakup": breakup2,
                })
                position = reentry_side * qty
            current_expiry_idx += 1
            if current_expiry_idx >= len(expiries):
                current_expiry_idx = None

    return out, fut_closes, roll_events, trade_log


def build_kw(strategy, bars, fut_closes, lot_size, initial, rf, mar, trials,
             roll_events=None):
    if not bars:
        raise ValueError("at least two sessions are required for an annualized tearsheet")
    if len(fut_closes) != len(bars):
        raise ValueError("benchmark closes must align with strategy bars")
    if not isfinite(initial) or initial <= 0:
        raise ValueError("initial equity must be positive")
    dates = [dt for dt, _, _ in bars]
    closes = [c for _, c, _ in bars]
    n_bars = len(bars)
    day_index = {}
    for i, dt in enumerate(dates):
        day_index[dt.date()] = i
    days = sorted(day_index)
    n_days = len(days)
    if n_days < 2:
        raise ValueError("at least two sessions are required for an annualized tearsheet")

    equity, net_lots, realized_pnl, trades, total_fees = ts.ledger(
        bars, lot_size, initial)
    for dt, value in zip(dates, equity):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"account insolvent at {dt:%Y-%m-%d %H:%M}: "
                             f"equity {value}")
    day_equity = [equity[day_index[d]] for d in days]
    rets = [day_equity[i] / day_equity[i - 1] - 1 for i in range(1, n_days)]
    rets.insert(0, day_equity[0] / initial - 1)

    years = (days[-1] - days[0]).days / 365.25
    ppy = n_days / years
    mean_r, sd_r, skew, kurt = ts.moments(rets)
    ann_ret = mean_r * ppy
    ann_vol = sd_r * sqrt(ppy)
    total = equity[-1] / initial - 1
    cagr = (equity[-1] / initial) ** (1 / years) - 1

    dd, peaks, episodes = ts.drawdowns(equity, initial)
    dd_pct = [d / p * 100 for d, p in zip(dd, peaks)]
    worst = min(range(n_bars), key=lambda i: dd[i])
    worst_p = min(range(n_bars), key=lambda i: dd_pct[i])
    worst_ep = min(episodes,
                   key=lambda e: e["depth"] / peaks[e["trough_i"]],
                   default=None)
    depths = [e["depth"] for e in episodes]
    depths_pct = [e["depth"] / peaks[e["trough_i"]] * 100 for e in episodes]

    def ep_days(e):
        start = dates[e["peak_i"]].date()
        end = dates[e["recover"]].date() if e["recover"] is not None else days[-1]
        return (end - start).days

    def ep_bars(e):
        end = e["recover"] if e["recover"] is not None else n_bars - 1
        return sum(1 for i in range(e["peak_i"], end + 1) if dd[i] < 0)

    underwater_days = sum(ep_days(e) for e in episodes)
    span_days = (days[-1] - days[0]).days
    rec = worst_ep["recover"] if worst_ep is not None else None
    longest_ep = max(episodes, key=ep_days, default=None)
    cur_ep = episodes[-1] if episodes and episodes[-1]["recover"] is None else None
    cur_days = (days[-1] - dates[cur_ep["peak_i"]].date()).days if cur_ep else 0

    mar_r = mar / ppy
    rf_r = rf / ppy
    downside = ts.downside_deviation(rets, mar_r)
    omega_num = sum(max(r - mar_r, 0.0) for r in rets)
    omega_den = sum(max(mar_r - r, 0.0) for r in rets)
    ulcer = ts.ulcer_index(dd_pct)
    maxdd_pct_dec = dd_pct[worst_p] / 100
    sharpe = (mean_r - rf_r) / sd_r * sqrt(ppy) if sd_r else float("nan")
    sortino = (mean_r - mar_r) / downside * sqrt(ppy) if downside else float("nan")
    calmar = cagr / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan")
    omega = omega_num / omega_den if omega_den else float("nan")

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    def streaks(pred):
        best = cur = 0
        for p in pnls:
            cur = cur + 1 if pred(p) else 0
            best = max(best, cur)
        return best

    best_t = max((t for t in trades if t["pnl"] > 0),
                 key=lambda t: t["pnl"], default=None)
    worst_t = min((t for t in trades if t["pnl"] <= 0),
                  key=lambda t: t["pnl"], default=None)
    tstats = {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "payoff": ((gross_win / len(wins)) / (gross_loss / len(losses)))
        if wins and losses and gross_loss else 0.0,
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "max_win": best_t["pnl"] if best_t else None,
        "max_loss": worst_t["pnl"] if worst_t else None,
        "max_win_streak": streaks(lambda p: p > 0),
        "max_loss_streak": streaks(lambda p: p <= 0),
        "avg_hold_days": (sum(
            (t["exit"] - t["entry"]).total_seconds() / 86400 for t in trades
        ) / len(trades)) if trades else 0.0,
        "avg_hold_bars": sum(t["bars"] for t in trades) / len(trades) if trades else 0.0,
    }

    month_end = {}
    for d, e in zip(days, day_equity):
        month_end[(d.year, d.month)] = e
    monthly, annual = {}, {}
    prev = initial
    for key in sorted(month_end):
        r = month_end[key] / prev - 1
        monthly[key] = r
        annual.setdefault(key[0], []).append(r)
        prev = month_end[key]
    annual = {y: _math_prod(rs) - 1 for y, rs in annual.items()}

    monthly_points, annual_points = {}, {}
    for t in trades:
        key = (t["exit"].year, t["exit"].month)
        monthly_points[key] = monthly_points.get(key, 0.0) + t.get("points", 0.0)
    for key, val in monthly_points.items():
        annual_points[key[0]] = annual_points.get(key[0], 0.0) + val

    roll_reentries = {
        e["date"] for e in roll_events or []
        if e["action"] in ("rollover", "contract_switch")
    }
    for dt, price in zip(dates, fut_closes):
        if not isfinite(price) or price <= 0:
            raise ValueError(f"non-positive benchmark price at {dt:%Y-%m-%d %H:%M}")
    # Anchor at the first bar close, then compound only observed bar returns.
    bh_bars = [initial]
    for i in range(1, n_bars):
        r = (0.0 if dates[i] in roll_reentries
             else fut_closes[i] / fut_closes[i - 1] - 1)
        bh_bars.append(bh_bars[-1] * (1 + r))
    bh_equity = [bh_bars[day_index[d]] for d in days]
    bh_dd, bh_peaks, _ = ts.drawdowns(bh_equity, initial)
    bh_rets = [bh_equity[i] / bh_equity[i - 1] - 1 for i in range(1, n_days)]
    bh_rets.insert(0, bh_equity[0] / initial - 1)
    b_mean, b_sd, _, _ = ts.moments(bh_rets)
    bench = {
        "total": bh_equity[-1] / initial - 1,
        "final": bh_equity[-1],
        "cagr": (bh_equity[-1] / initial) ** (1 / years) - 1,
        "maxdd": min(d / p for d, p in zip(bh_dd, bh_peaks)),
        "sharpe": (b_mean - rf_r) / b_sd * sqrt(ppy) if b_sd else float("nan"),
        "ir": float("nan"), "te": float("nan"),
        "beta": float("nan"), "corr": float("nan"), "alpha": float("nan"),
        "up_cap": float("nan"), "down_cap": float("nan"),
    }
    if sd_r:
        active = [a - b for a, b in zip(rets, bh_rets)]
        a_mean, a_sd, _, _ = ts.moments(active)
        cov = sum((a - mean_r) * (b - b_mean)
                  for a, b in zip(rets, bh_rets)) / (n_days - 1)
        var_b = sum((b - b_mean) ** 2 for b in bh_rets) / (n_days - 1)
        beta = cov / var_b if var_b else float("nan")
        corr = cov / (sd_r * b_sd) if b_sd else float("nan")
        up = [(a, b) for a, b in zip(rets, bh_rets) if b > 0]
        dn = [(a, b) for a, b in zip(rets, bh_rets) if b < 0]
        bench.update({
            "ir": a_mean / a_sd * sqrt(ppy) if a_sd else float("nan"),
            "te": a_sd * sqrt(ppy),
            "beta": beta,
            "corr": corr,
            "alpha": ((mean_r - rf_r - beta * (b_mean - rf_r)) * ppy
                      if isfinite(beta) else float("nan")),
            "up_cap": ((sum(a for a, _ in up) / len(up)) /
                       (sum(b for _, b in up) / len(up))
                       if up and sum(b for _, b in up) else float("nan")),
            "down_cap": ((sum(a for a, _ in dn) / len(dn)) /
                         (sum(b for _, b in dn) / len(dn))
                         if dn and sum(b for _, b in dn) else float("nan")),
        })

    sr_period = (mean_r - rf_r) / sd_r if sd_r else float("nan")
    psr0 = ts.psr(n_days, sr_period, skew, kurt) if sd_r else float("nan")
    sr_target = 1.0 / sqrt(ppy)
    mintrl = (
        0.0 if sd_r and ts.psr(n_days, sr_period, skew, kurt, sr_target) >= 0.95
        else ts.min_track_record(sr_period, sr_target, skew, kurt)
        if sd_r else float("nan")
    )
    var_sharpe = ts.sharpe_var(n_days, sr_period, skew, kurt) * ppy if sd_r else 0.0
    emax = ts.expected_max_sharpe(trials, var_sharpe)
    dsr = (ts.psr(n_days, sr_period, skew, kurt, sr_star=emax / sqrt(ppy))
           if sd_r and trials > 0 else float("nan"))

    top5_eps = sorted(
        episodes, key=lambda e: e["depth"] / peaks[e["trough_i"]])[:5]

    kw = dict(
        strategy=strategy,
        data=(f"synthetic futures from {NIFTY_CSV.name} (option leg prices) + "
              "real futures signals from new_data.csv"),
        n_bars=n_bars, n_days=n_days, n_trades=len(trades),
        d0=days[0], d1=days[-1], years=years,
        initial=initial, final=equity[-1], pnl=equity[-1] - initial, total=total,
        final_realized=initial + realized_pnl[-1],
        final_unrealized=equity[-1] - initial - realized_pnl[-1],
        cagr=cagr, ann_ret=ann_ret, ann_vol=ann_vol, ppy=ppy,
        best_day_r=max(rets),
        best_day=days[max(range(n_days), key=lambda i: rets[i])],
        worst_day_r=min(rets),
        worst_day=days[min(range(n_days), key=lambda i: rets[i])],
        pos_days=sum(1 for r in rets if r > 0), monthly=monthly,
        rf=rf, mar=mar, sharpe=sharpe, sortino=sortino, calmar=calmar,
        omega=omega, ulcer=ulcer,
        upi=ann_ret * 100 / ulcer if ulcer else float("nan"),
        recovery=total / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan"),
        profit_factor=gross_win / gross_loss if gross_loss else float("nan"),
        maxdd=dd[worst_p], maxdd_pct=maxdd_pct_dec,
        maxdd_peak=(dates[worst_ep["peak_i"]] if worst_ep else dates[0]),
        maxdd_trough=dates[worst_p],
        maxdd_len_days=((dates[worst_p] - dates[worst_ep["peak_i"]]).days
                        if worst_ep else 0),
        maxdd_rec=(f"{dates[rec]:%d %b %Y}" if rec is not None
                   else "not yet (open)" if worst_ep else "n/a"),
        cur_dd=dd[-1] / peaks[-1], cur_days=cur_days,
        avg_dd=sum(depths) / len(depths) if depths else 0.0,
        avg_dd_pct=sum(depths_pct) / len(depths_pct) if depths_pct else 0.0,
        n_ep=len(episodes),
        longest_days=ep_days(longest_ep) if longest_ep else 0,
        longest_fills=ep_bars(longest_ep) if longest_ep else 0,
        underwater_pct=underwater_days / span_days if span_days else 0.0,
        underwater_days=underwater_days, span_days=span_days,
        top5=[e["depth"] for e in top5_eps],
        peak_at_episode=[peaks[e["trough_i"]] for e in top5_eps],
        trades=tstats,
        max_win_date=best_t["exit"] if best_t else None,
        max_loss_date=worst_t["exit"] if worst_t else None,
        total_fees=total_fees, gross_win=gross_win,
        cost_model="synthetic futures + options costs + expiry rollover",
        exposure=sum(1 for q in net_lots if not is_zero(q)) / n_bars,
        var95=ts.quantile(rets, 0.05), var99=ts.quantile(rets, 0.01),
        cvar95=ts.cvar(rets, 0.05), cvar99=ts.cvar(rets, 0.01),
        skew=skew, kurt=kurt,
        tail=(abs(ts.quantile(rets, 0.95)) / abs(ts.quantile(rets, 0.05))
              if ts.quantile(rets, 0.05) else float("nan")),
        pos_months=sum(1 for r in monthly.values() if r > 0),
        best_m=max(monthly.values()), worst_m=min(monthly.values()),
        psr=psr0, mintrl=mintrl, trials=trials, emax=emax, dsr=dsr,
        annual=annual, bench=bench,
        monthly_points=monthly_points, annual_points=annual_points,
        synthetic=None,
        synthetic_sections=[],
        synthetic_warnings=[],
    )
    return kw, equity, dates, closes, peaks, dd, dd_pct, worst, worst_p, bh_bars


def _date_arg(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def _prompt_date_window(args):
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
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                print("Please enter a date in YYYY-MM-DD format, or press Enter.")

    if args.date_from is None or args.date_till is None:
        print("\nREPORT DATE WINDOW")
        print("Enter dates as YYYY-MM-DD. Press Enter to use the full available range.")
        if args.date_from is None:
            args.date_from = ask_date("From date (Enter = earliest available): ")
        if args.date_till is None:
            args.date_till = ask_date("To date   (Enter = latest available): ")

def _prompt_output_files(args):
    """Prompt for output file names when running interactively."""
    # Only prompt on TTY
    if not sys.stdin.isatty():
        return

    # PNG output
    if args.out == OUT_PNG and "--out" not in sys.argv:
        print("\nOUTPUT FILES")
        raw = input(f"PNG file name (Enter = {OUT_PNG.name}): ").strip()
        if raw:
            args.out = Path(raw)

    # CSV trade log
    if args.csv is None and "--csv" not in sys.argv:
        raw = input("Export trades CSV? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = input(f"  CSV file name (Enter = synthetic_tearsheet_trades.csv): ").strip()
            args.csv = Path(name) if name else ROOT / "reports" / "trade-logs" / "synthetic_tearsheet_trades.csv"

    # HTML report
    if args.export is None and "--export" not in sys.argv:
        raw = input("Export HTML report? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = input(f"  HTML file name (Enter = synthetic_tearsheet_report.html): ").strip()
            args.export = Path(name) if name else ROOT / "reports" / "tearsheets" / "synthetic_tearsheet_report.html"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "strategy",
        nargs="?",
        choices=list(STRATEGIES),
        default=None,
        help=f"which bin/ strategy to run (default: {DEFAULT_STRATEGY}; "
             "prompts on a TTY when omitted)",
    )
    ap.add_argument("--out", type=Path, default=OUT_PNG, help="output PNG path")
    ap.add_argument("--initial", type=float, default=None,
                    help="account balance for %% drawdowns (default: the cost config's)")
    ap.add_argument("--rf", type=float, default=0.0,
                    help="annual risk-free rate for the Sharpe ratio (default 0.0)")
    ap.add_argument("--mar", type=float, default=0.0,
                    help="minimum acceptable return for Sortino/Omega (default 0.0)")
    ap.add_argument("--trials", type=int, default=0,
                    help="number of strategy variants tried; deflates the Sharpe ratio")
    ap.add_argument(
        "--export",
        nargs="?",
        type=Path,
        const=ROOT / "reports" / "tearsheets" / "synthetic_tearsheet_report.html",
        default=None,
        metavar="PATH",
        help="write a styled, self-contained HTML report instead of printing "
             "the statistics",
    )
    ap.add_argument(
        "--csv",
        nargs="?",
        type=Path,
        const=ROOT / "reports" / "trade-logs" / "synthetic_tearsheet_trades.csv",
        default=None,
        metavar="PATH",
        help="write a CSV trade log with timestamps, legs, prices, fees "
             "(default: synthetic_tearsheet_trades.csv)",
    )
    cost_cli.add_arguments(ap)

    # Remove knobs that don't apply to synthetic futures (premiums come from
    # data, futures slippage is not used).
    for _key in ("slip_ticks", "slip_range_pct", "call_premium_pct",
                 "put_premium_pct"):
        for _action in list(ap._actions):
            if getattr(_action, "dest", None) == _key:
                ap._actions.remove(_action)
                for _g in ap._action_groups:
                    if _action in _g._group_actions:
                        _g._group_actions.remove(_action)
                for _opt in _action.option_strings:
                    ap._option_string_actions.pop(_opt, None)

    from_dest = ap._option_string_actions["--from"].dest
    till_dest = ap._option_string_actions["--till"].dest
    args = ap.parse_args()
    if args.trials < 0:
        sys.exit("--trials must be non-negative")

    args.date_from = getattr(args, from_dest, None)
    args.date_till = getattr(args, till_dest, None)
    if isinstance(args.date_from, str):
        args.date_from = _date_arg(args.date_from)
    if isinstance(args.date_till, str):
        args.date_till = _date_arg(args.date_till)

    strategy = resolve_strategy(args.strategy)

    # Load option data first so we can clamp the strategy to option coverage
    times_opt, nets_opt, strikes_opt, calls_opt, puts_opt = load_option_net(NIFTY_CSV)
    if not times_opt:
        sys.exit(f"no tradeable option minutes in {NIFTY_CSV}")
    opt_net = make_option_lookup(times_opt, nets_opt, strikes_opt, calls_opt, puts_opt)

    # Load next month (month2) option data for rollover re-entry (CSV).
    nm_times, nm_nets, nm_strikes, nm_calls, nm_puts = load_next_month_from_csv(NIFTY_MONTH2_CSV)
    if not nm_times:
        sys.exit(f"no tradeable month2 option minutes in {NIFTY_MONTH2_CSV}")
    next_month_lookup = make_next_month_lookup(nm_times, nm_nets, nm_strikes, nm_calls, nm_puts)
    month2_till = datetime.fromtimestamp(nm_times[-1], IST).date()
    print(f"Next month (month2 CSV) data loaded: {len(nm_times)} tradeable minutes")

    # Read futures data bounds for clamping
    try:
        with DATA_CSV.open("rb") as f:
            f.readline()  # header
            f.seek(0, 2)
            pos, tail = f.tell(), b""
            while pos > 0 and tail.count(b"\n") < 2:
                step = min(pos, 4096)
                pos -= step
                f.seek(pos)
                tail = f.read(step) + tail
            last_line = [ln for ln in tail.split(b"\n") if ln.strip()][-1]
            data_till = datetime.strptime(
                last_line.split(b",", 1)[0].decode().strip(), "%d/%m/%Y").date()
    except (OSError, ValueError, IndexError):
        data_till = None

    # Clamp strategy to option-data coverage and futures data range
    opt_start = times_opt[0].date()
    opt_till = times_opt[-1].date()
    till_cap = (min(opt_till, month2_till) if data_till is None
                else min(opt_till, month2_till, data_till))
    if args.date_from is None or args.date_from < opt_start:
        args.date_from = opt_start
    if args.date_till is None or args.date_till > till_cap:
        args.date_till = till_cap

    _prompt_date_window(args)
    # Re-apply clamping after prompting (user dates take priority)
    if args.date_from is not None and args.date_from < opt_start:
        args.date_from = opt_start
    if args.date_till is not None and args.date_till > till_cap:
        args.date_till = till_cap

    setattr(args, from_dest,
            args.date_from.isoformat() if args.date_from is not None else None)
    setattr(args, till_dest,
            args.date_till.isoformat() if args.date_till is not None else None)

    cfg = cost_cli.resolve(args, ask_period=False)
    _prompt_output_files(args)
    if args.initial is not None:
        if args.initial_balance is not None and args.initial != args.initial_balance:
            sys.exit("--initial and --initial-balance must match when both are supplied")
        cfg = replace(cfg, initial_balance=args.initial)

    meta = cost_cli.schema()
    lot_size = cfg.lot_size
    initial = cfg.initial_balance
    if not isfinite(initial) or initial <= 0:
        sys.exit("initial balance must be finite and positive")

    # Run the strategy with --costs none: we apply option costs manually
    # through OptionCostModel below, so the binary must not double-charge.
    argv = cfg.argv()
    argv[argv.index("--costs") + 1] = "none"

    out, err = run_backtest(STRATEGIES[strategy], argv)
    import backtest
    backtest.TAGS = ts.parse_tags(out)
    bars = ts.parse_bars(out)
    if not any(f for _, _, f in bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    trading_dates = option_trading_dates(times_opt, nm_times)
    expiries = build_expiry_calendar(
        trading_dates, times_opt[0].date(), max(times_opt[-1].date(), month2_till))
    print(f"\nNIFTY expiries in range: {expiries[0]} to {expiries[-1]} "
          f"({len(expiries)} expiries)")
    print("Rollover rule: square off at 15:20 on expiry day, "
          "re-enter next month at 15:21\n")

    # No fees when "none" preset is selected
    cost_model = None if cfg.preset == "none" else OptionCostModel(meta, cfg.values)

    synth_bars, fut_closes, roll_events, trade_log = synthetic_bars_with_rollover(
        bars, opt_net, times_opt[0], times_opt[-1], expiries, cost_model, lot_size,
        next_month_lookup)
    if not synth_bars:
        sys.exit(f"no executable bars within option coverage")

    position_rolls = sum(e["action"] == "square_off" for e in roll_events)
    print(f"Contract switches: {len(roll_events)}; position rolls: {position_rolls}")
    print(f"Trade log entries: {len(trade_log)}")

    # Write CSV trade log if requested
    if args.csv is not None:
        import csv as csv_mod
        csv_path = args.csv
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            writer = csv_mod.writer(f)
            writer.writerow([
                "timestamp", "action", "futures_side", "option_legs",
                "qty", "price", "call_premium", "put_premium",
                "fee", "contract", "is_rollover",
                "brokerage", "txn_fee", "sebi_fee", "stt", "stamp", "gst",
                "slippage_call", "slippage_put",
            ])
            for entry in trade_log:
                bk = entry.get("cost_breakup", {})
                writer.writerow([
                    entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    entry["action"],
                    entry["futures_side"],
                    entry["option_legs"],
                    entry["qty"],
                    f"{entry['price']:.2f}",
                    f"{bk['call_premium']:.4f}",
                    f"{bk['put_premium']:.4f}",
                    f"{entry['fee']:.2f}",
                    entry["contract"],
                    entry["is_rollover"],
                    f"{bk.get('brokerage', 0):.2f}",
                    f"{bk.get('txn', 0):.2f}",
                    f"{bk.get('sebi', 0):.2f}",
                    f"{bk.get('stt', 0):.2f}",
                    f"{bk.get('stamp', 0):.2f}",
                    f"{bk.get('gst', 0):.2f}",
                    f"{bk.get('slippage_call', 0):.4f}",
                    f"{bk.get('slippage_put', 0):.4f}",
                ])
        print(f"trade log saved to: {csv_path}")

    try:
        (kw, equity, dates, closes, peaks, dd, dd_pct, worst, worst_p,
         bh_bars) = build_kw(strategy, synth_bars, fut_closes, lot_size, initial,
                             args.rf, args.mar, args.trials, roll_events)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.initial is not None or args.initial_balance is not None:
        kw["initial_source"] = "command line"

    content = ts.build_report(kw)
    content["sections"] = [s for s in content["sections"] if s[0] != "COSTS"]
    if args.export is None:
        ts.print_report(content)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    money = FuncFormatter(lambda v, _: f"{v:,.0f}")
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(13, 11), sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1, 1], "hspace": 0.14},
    )

    ax1.plot(dates, equity, color="tab:blue", lw=1.3,
             label="Synthetic-futures equity (mark-to-market, option leg prices)")
    ax1.plot(dates, bh_bars, color="0.45", lw=1.0, ls="-.",
             label=f"Buy & hold synthetic ({NIFTY_CSV.name})")
    ax1.plot(dates, peaks, color="0.55", lw=1.0, ls="--",
             label="Equity high-water mark")
    ax1.fill_between(dates, equity, peaks, color="tab:red", alpha=0.30,
                     label="Drawdown (underwater)")
    ax1.fill_between(dates, equity, initial, where=[v >= initial for v in equity],
                     color="tab:green", alpha=0.10, label="Above starting balance")
    ax1.axhline(initial, color="0.4", lw=0.9, ls=":")
    ax1.annotate(f"start {initial:,.0f}", (dates[0], initial),
                 textcoords="offset points", xytext=(4, -14), fontsize=8, color="0.35")
    ax1.plot([dates[worst_p]], [equity[worst_p]], "o", color="tab:red", ms=6, zorder=5)
    annotation_side = 1 if worst_p <= len(dates) // 2 else -1
    ax1.annotate(
        f"max drawdown\n{dd[worst_p]:,.0f} ({dd_pct[worst_p]:.1f}% of peak)\n"
        f"{dates[worst_p]:%d %b %Y}",
        (dates[worst_p], equity[worst_p]), textcoords="offset points",
        xytext=(10 * annotation_side, -52),
        ha="left" if annotation_side > 0 else "right",
        fontsize=9, color="tab:red", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="tab:red", lw=1.0),
    )
    ax1.set_title(
        f"{strategy} — synthetic futures with expiry rollover "
        "(option leg prices, square-off on expiry day, re-enter next month)",
        fontsize=14, pad=12,
    )
    ax1.set_ylabel("Account equity")
    ax1.yaxis.set_major_formatter(money)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=9)

    ax2.fill_between(dates, 0, dd_pct, color="tab:red", alpha=0.45)
    ax2.plot(dates, dd_pct, color="tab:red", lw=1.1)
    ax2.axhline(kw["avg_dd_pct"], color="0.35", lw=1.0, ls="--",
                label=f"average drawdown {kw['avg_dd_pct']:.2f}%")
    ax2.plot([dates[worst_p]], [dd_pct[worst_p]], "o", color="darkred", ms=6, zorder=5)
    ax2.annotate(
        f"max {dd_pct[worst_p]:.2f}%  ({dd[worst_p]:,.0f})\n{dates[worst_p]:%d %b %Y}",
        (dates[worst_p], dd_pct[worst_p]), textcoords="offset points",
        xytext=(14, -6), fontsize=9, color="darkred", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="darkred", lw=1.0),
    )
    ax2.legend(loc="lower left", framealpha=0.9, fontsize=9)
    ax2.set_title("Underwater curve — drawdown from the equity high-water mark",
                  fontsize=12, pad=8)
    ax2.set_ylabel("Drawdown from peak (%)")
    ax2.set_ylim(min(dd_pct) * 1.25, 1.0)
    ax2.grid(True, alpha=0.3)

    window = 126
    day_index = {}
    for i, dt in enumerate(dates):
        day_index[dt.date()] = i
    days_list = sorted(day_index)
    day_equity = [equity[day_index[d]] for d in days_list]
    rets = [day_equity[i] / day_equity[i - 1] - 1 for i in range(1, len(days_list))]
    rets.insert(0, day_equity[0] / initial - 1)
    rf_r = args.rf / kw["ppy"]
    roll_sharpe = []
    for i in range(window - 1, len(days_list)):
        w = rets[i - window + 1:i + 1]
        m, s, _, _ = ts.moments(w)
        roll_sharpe.append((days_list[i],
                            (m - rf_r) / s * sqrt(kw["ppy"]) if s else float("nan")))
    ax3.plot([d for d, _ in roll_sharpe], [v for _, v in roll_sharpe],
             color="tab:purple", lw=1.1,
             label=f"rolling {window}-session Sharpe")
    ax3.axhline(kw["sharpe"], color="0.35", lw=1.0, ls="--",
                label=f"full-period Sharpe {kw['sharpe']:.2f}")
    ax3.axhline(0.0, color="0.7", lw=0.8, ls=":")
    ax3.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax3.set_title("Rolling Sharpe ratio — is the edge persistent or a spike?",
                  fontsize=12, pad=8)
    ax3.set_ylabel("Sharpe (annualized)")
    ax3.set_xlabel("Date")
    ax3.grid(True, alpha=0.3)

    ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax3.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))

    fig.text(
        0.99, 0.005,
        f"CAGR {kw['cagr']:+.2%}  |  Sharpe {kw['sharpe']:.2f}  |  Sortino {kw['sortino']:.2f}  |  "
        f"Calmar {kw['calmar']:.2f}  |  max DD {dd_pct[worst_p]:.2f}%  |  "
        f"underwater {kw['underwater_pct']:.0%}  |  script: scripts/synthetic_tearsheet.py",
        ha="right", fontsize=8.5, color="0.35",
    )
    fig.subplots_adjust(hspace=0.14, left=0.085, right=0.97, top=0.955, bottom=0.06)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150, facecolor="white")
    print(f"plot saved to:  {args.out}")

    if args.export is not None:
        ts.write_html_report(args.export, kw, content, args.out.read_bytes())
        print(f"report saved to: {args.export}")


if __name__ == "__main__":
    main()
