"""Option-derived synthetic prices, execution costs, and contract rollover."""

from __future__ import annotations

import bisect
import calendar
import csv
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from math import isfinite
from pathlib import Path

from .backtest import Bar, Fill
from .costs import option_leg_breakup
from .ledger import sign

ROOT = Path(__file__).resolve().parent.parent
FUTURES_CSV = ROOT / "data" / "raw" / "new_data.csv"
NIFTY_CSV = ROOT / "data" / "raw" / "nifty_atm_month1.csv"
NIFTY_MONTH2_CSV = ROOT / "data" / "raw" / "nifty_atm_month2.csv"
FUTURES_EMITTER = ROOT / "research" / "emitters" / "ohlc_csv_emitter.py"
OPTIONS_EMITTER = ROOT / "research" / "emitters" / "synthetic_ohlc_emitter.py"
EXTREMA_EMITTER = ROOT / "research" / "emitters" / "synthetic_ohlc_extrema_emitter.py"

EXPIRY_RULES = ((date(2000, 1, 1), 3), (date(2025, 9, 1), 1))
ROLLOVER_CUTOFF = time(15, 20)
BUY, SELL = +1, -1


def _weekday_rule(day):
    weekday = EXPIRY_RULES[0][1]
    for effective, candidate in EXPIRY_RULES:
        if day >= effective:
            weekday = candidate
    return weekday


def nominal_monthly_expiry(year, month, at):
    weekday = _weekday_rule(at)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def build_expiry_calendar(trading_dates, start, end):
    """Adjust a nominal expiry only when later sessions establish a closure."""
    expiries = []
    cursor = date(start.year, start.month, 1)
    last_month = date(end.year, end.month, 1)
    for _ in range(3):
        last_month = date(
            last_month.year + (last_month.month == 12),
            last_month.month % 12 + 1,
            1,
        )
    while cursor <= last_month:
        nominal = nominal_monthly_expiry(cursor.year, cursor.month, at=cursor)
        adjusted = nominal
        if nominal not in trading_dates and any(
            nominal < day <= nominal + timedelta(days=7) for day in trading_dates
        ):
            candidate = nominal - timedelta(days=1)
            earliest = max(date(cursor.year, cursor.month, 1), nominal - timedelta(days=7))
            while candidate >= earliest and candidate not in trading_dates:
                candidate -= timedelta(days=1)
            if candidate >= earliest:
                adjusted = candidate
        expiries.append(adjusted)
        cursor = date(
            cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1
        )
    return sorted(set(expiries))


class OptionCostModel:
    """Two-leg option execution costs using the OCaml schema rates."""

    def __init__(self, meta, values=None):
        self.rates = meta["rates"]
        defaults = meta["defaults"]
        values = values or {}
        self.fee_per_order = float(values.get("fee_per_order", defaults["fee_per_order"]))
        self.opt_slip_ticks = float(
            values.get("opt_slip_ticks", defaults["opt_slip_ticks"])
        )
        self.opt_slip_pct = float(values.get("opt_slip_pct", defaults["opt_slip_pct"]))
        self.opt_tick_size = float(defaults["opt_tick_size"])
        self.slippage_multiplier = 1.0

    def slippage(self, premium):
        return max(
            0.0,
            self.slippage_multiplier
            * (
                self.opt_slip_ticks * self.opt_tick_size
                + self.opt_slip_pct / 100.0 * premium
            ),
        )

    def execution_price(self, side, open_price):
        return max(
            self.opt_tick_size, open_price + side * self.slippage(open_price)
        )

    def fee(self, side, execution_price, lots, lot_size):
        return sum(
            self._leg_breakup(side, execution_price, lots, lot_size).values()
        )

    def futures_fee(self, side, call_price, put_price, lots, lot_size):
        if side > 0:
            return self.fee(BUY, call_price, lots, lot_size) + self.fee(
                SELL, put_price, lots, lot_size
            )
        return self.fee(SELL, call_price, lots, lot_size) + self.fee(
            BUY, put_price, lots, lot_size
        )

    def futures_cost(self, side, call_open, put_open, lots, lot_size):
        call_side = BUY if side > 0 else SELL
        put_side = -call_side
        call_price = self.execution_price(call_side, call_open)
        put_price = self.execution_price(put_side, put_open)
        return {
            "call_price": call_price,
            "put_price": put_price,
            "slippage": call_side * (call_price - call_open)
            + put_side * (put_price - put_open),
            "fee": self.futures_fee(side, call_price, put_price, lots, lot_size),
            "breakup": self.futures_fee_breakup(
                side,
                call_price,
                put_price,
                lots,
                lot_size,
                raw_call_price=call_open,
                raw_put_price=put_open,
            ),
        }

    def futures_fee_breakup(
        self,
        side,
        call_price,
        put_price,
        lots,
        lot_size,
        raw_call_price=None,
        raw_put_price=None,
    ):
        call_side = BUY if side > 0 else SELL
        put_side = -call_side
        leg1 = self._leg_breakup(call_side, call_price, lots, lot_size)
        leg2 = self._leg_breakup(put_side, put_price, lots, lot_size)

        def raw_price(leg_side, execution_price):
            fixed = self.slippage_multiplier * self.opt_slip_ticks * self.opt_tick_size
            percentage = self.slippage_multiplier * self.opt_slip_pct / 100.0
            return (execution_price - leg_side * fixed) / (1 + leg_side * percentage)

        if raw_call_price is None:
            raw_call_price = raw_price(call_side, call_price)
        if raw_put_price is None:
            raw_put_price = raw_price(put_side, put_price)
        return {
            "brokerage": leg1["brokerage"] + leg2["brokerage"],
            "txn": leg1["txn"] + leg2["txn"],
            "sebi": leg1["sebi"] + leg2["sebi"],
            "stt": leg1["stt"] + leg2["stt"],
            "stamp": leg1["stamp"] + leg2["stamp"],
            "gst": leg1["gst"] + leg2["gst"],
            "slippage_call": call_side * (call_price - raw_call_price),
            "slippage_put": put_side * (put_price - raw_put_price),
        }

    def _leg_breakup(self, side, execution_price, lots, lot_size):
        return option_leg_breakup(
            side,
            execution_price,
            lots,
            lot_size,
            self.fee_per_order,
            self.rates,
        )


@lru_cache(maxsize=4)
def load_option_net(csv_path):
    """Load synchronized same-strike call and put closes."""
    legs = {}
    with csv_path.open(newline="") as file:
        reader = csv.reader(file)
        header = next(reader, None)
        if not header:
            return [], [], [], [], []
        indexes = {name: header.index(name) for name in ("Date", "Time", "strike", "option_type", "close")}
        for row in reader:
            if not row or len(row) <= indexes["close"]:
                continue
            dt = datetime.strptime(
                row[indexes["Date"]].strip() + row[indexes["Time"]].strip(),
                "%d/%m/%Y%H%M",
            )
            option_type = row[indexes["option_type"]].strip().upper()
            entry = legs.setdefault(dt, {})
            entry[option_type] = float(row[indexes["close"]])
            entry[f"{option_type}_strike"] = float(row[indexes["strike"]])

    times, nets, strikes, calls, puts = [], [], [], [], []
    for dt in sorted(legs):
        value = legs[dt]
        if (
            "CALL" in value
            and "PUT" in value
            and value["CALL_strike"] == value["PUT_strike"]
        ):
            times.append(dt)
            nets.append(value["CALL"] - value["PUT"])
            strikes.append(value["CALL_strike"])
            calls.append(value["CALL"])
            puts.append(value["PUT"])
    return times, nets, strikes, calls, puts


def make_option_lookup(times, nets, strikes, calls=None, puts=None):
    """Return the latest same-session option quote at or before a datetime."""
    if not times:
        return lambda _: None
    calls = calls or [0.0] * len(times)
    puts = puts or [0.0] * len(times)

    def lookup(dt):
        index = bisect.bisect_right(times, dt) - 1
        if index < 0 or times[index].date() != dt.date():
            return None
        return nets[index], strikes[index], calls[index], puts[index]

    return lookup


def option_trading_dates(front_times, month2_times=()):
    return {dt.date() for dt in front_times} | {dt.date() for dt in month2_times}


def _require_option_leg(lookup, dt, contract):
    leg = lookup(dt) if lookup is not None else None
    if leg is None or len(leg) < 4:
        raise ValueError(f"no same-session {contract} option quote at {dt}")
    return leg


def _fill_cost(cost_model, side, leg, quantity, lot_size):
    if cost_model is None:
        return 0.0, 0.0, {
            "brokerage": 0,
            "txn": 0,
            "sebi": 0,
            "stt": 0,
            "stamp": 0,
            "gst": 0,
            "slippage_call": 0,
            "slippage_put": 0,
            "call_premium": leg[2],
            "put_premium": leg[3],
        }
    lots = quantity / lot_size
    cost = cost_model.futures_cost(side, leg[2], leg[3], lots, lot_size)
    breakup = cost["breakup"]
    breakup["call_premium"] = cost["call_price"]
    breakup["put_premium"] = cost["put_price"]
    return cost["slippage"], cost["fee"], breakup


def synthetic_close_series(times, nets, strikes):
    return [(dt, net + strike) for dt, net, strike in zip(times, nets, strikes)]


def first_stop_crossing(minute_bars, bar_end, side, stop_price):
    start = bar_end - timedelta(minutes=29)
    index = bisect.bisect_left(minute_bars, (start, float("-inf")))
    for dt, close in minute_bars[index:]:
        if dt > bar_end:
            break
        if (side > 0 and close >= stop_price) or (side < 0 and close <= stop_price):
            return dt
    return None


def _retime_expiry_stop_bars(bars, expiries, minute_bars):
    expiry_dates = set(expiries)
    minute_prices = dict(minute_bars)
    output = []
    for bar in bars:
        dt = bar.dt
        if dt.date() not in expiry_dates or dt.time() != time(15, 30) or not bar.fills:
            output.append(bar)
            continue
        moved, remaining = {}, []
        for fill in bar.fills:
            side, stop_price = fill.side, fill.price
            fill_dt = None
            warning = None
            if abs(stop_price - bar.close) <= 1e-9:
                warning = "bar-close fill cannot be retimed"
            else:
                fill_dt = first_stop_crossing(minute_bars, dt, side, stop_price)
                if fill_dt is None:
                    warning = "no matching one-minute stop crossing"
            if fill_dt is None:
                remaining.append(fill)
                print(
                    f"warning: {warning} at {dt} for stop {stop_price}; using 15:30",
                    file=sys.stderr,
                )
                continue
            if fill_dt == dt:
                remaining.append(fill)
                continue
            reference = minute_prices[fill_dt]
            moved.setdefault((fill_dt, reference), []).append(fill)
        for (fill_dt, reference), fills in sorted(moved.items()):
            output.append(Bar(fill_dt, reference, tuple(fills)))
        output.append(replace(bar, fills=tuple(remaining)))
    return output


def synthetic_bars_with_rollover(
    bars,
    option_lookup,
    first_dt,
    last_dt,
    expiries,
    cost_model,
    lot_size,
    next_month_lookup=None,
    one_minute_closes=None,
):
    """Map source fills to option-derived prices and roll at each expiry."""
    if one_minute_closes is not None:
        bars = _retime_expiry_stop_bars(bars, expiries, one_minute_closes)
    expiry_dates = set(expiries)
    by_date = {}
    for bar in bars:
        if not first_dt <= bar.dt <= last_dt:
            continue
        if (
            one_minute_closes is None
            and bar.dt.date() in expiry_dates
            and bar.dt.time() == time(15, 30)
            and bar.fills
        ):
            print(
                f"warning: real-futures expiry fills at {bar.dt} remain at "
                "15:30; intrabar stop timing is not reconstructed",
                file=sys.stderr,
            )
        by_date.setdefault(bar.dt.date(), []).append(bar)
    if by_date:
        first_day, last_day = min(by_date), max(by_date)
        for expiry in expiries:
            if first_day <= expiry <= last_day:
                by_date.setdefault(expiry, [])

    output, futures_closes, roll_events, trade_log = [], [], [], []
    position = 0
    current_expiry_index = None

    def roll(bar_date):
        nonlocal position, current_expiry_index
        rollover_dt1 = datetime.combine(bar_date, ROLLOVER_CUTOFF)
        rollover_dt2 = rollover_dt1 + timedelta(minutes=1)
        leg1 = _require_option_leg(option_lookup, rollover_dt1, "front-month")
        leg2 = _require_option_leg(next_month_lookup, rollover_dt2, "month2 reentry")
        price1, price2 = leg1[0] + leg1[1], leg2[0] + leg2[1]
        if position == 0:
            output.extend((Bar(rollover_dt1, price1), Bar(rollover_dt2, price2)))
            futures_closes.extend((price1, price2))
            roll_events.append({
                "date": rollover_dt2,
                "action": "contract_switch",
                "qty": 0,
                "side": 0,
                "price": price2,
                "fee": 0.0,
            })
        else:
            quantity = abs(position)
            close_side = -1 if position > 0 else 1
            slippage1, fee1, breakup1 = _fill_cost(
                cost_model, close_side, leg1, quantity, lot_size
            )
            fill_price1 = price1 + close_side * slippage1
            output.append(Bar(
                rollover_dt1,
                price1,
                (Fill(close_side, fill_price1, quantity, fee1),),
            ))
            futures_closes.append(price1)
            roll_events.append({
                "date": rollover_dt1,
                "action": "square_off",
                "qty": quantity,
                "side": position,
                "price": fill_price1,
                "fee": fee1,
            })
            trade_log.append({
                "timestamp": rollover_dt1,
                "action": "ROLLOVER_SQUAREOFF",
                "futures_side": "SELL" if close_side < 0 else "BUY",
                "option_legs": "SELL CE, BUY PE" if close_side < 0 else "BUY CE, SELL PE",
                "qty": quantity,
                "price": fill_price1,
                "strike_price": leg1[1],
                "fee": fee1,
                "contract": "expiring",
                "is_rollover": True,
                "cost_breakup": breakup1,
            })
            position = 0

            reentry_side = -close_side
            slippage2, fee2, breakup2 = _fill_cost(
                cost_model, reentry_side, leg2, quantity, lot_size
            )
            fill_price2 = price2 + reentry_side * slippage2
            output.append(Bar(
                rollover_dt2,
                price2,
                (Fill(reentry_side, fill_price2, quantity, fee2),),
            ))
            futures_closes.append(price2)
            roll_events.append({
                "date": rollover_dt2,
                "action": "rollover",
                "qty": quantity,
                "side": reentry_side * quantity,
                "price": fill_price2,
                "fee": fee2,
            })
            trade_log.append({
                "timestamp": rollover_dt2,
                "action": "ROLLOVER_REENTRY",
                "futures_side": "BUY" if reentry_side > 0 else "SELL",
                "option_legs": "BUY CE, SELL PE" if reentry_side > 0 else "SELL CE, BUY PE",
                "qty": quantity,
                "price": fill_price2,
                "strike_price": leg2[1],
                "fee": fee2,
                "contract": "next_month",
                "is_rollover": True,
                "cost_breakup": breakup2,
            })
            position = reentry_side * quantity
        current_expiry_index += 1
        if current_expiry_index >= len(expiries):
            current_expiry_index = None

    for bar_date in sorted(by_date):
        day_bars = sorted(by_date[bar_date], key=lambda bar: bar.dt)
        if current_expiry_index is None:
            for index, expiry in enumerate(expiries):
                if expiry >= bar_date:
                    current_expiry_index = index
                    break
        is_expiry_day = (
            current_expiry_index is not None
            and bar_date == expiries[current_expiry_index]
        )
        contract_flipped = False

        for bar in day_bars:
            dt = bar.dt
            at_cutoff = (
                is_expiry_day and not contract_flipped and dt.time() > ROLLOVER_CUTOFF
            )
            if at_cutoff:
                contract_flipped = True
                roll(bar_date)

            on_next_month = is_expiry_day and contract_flipped
            contract = "month2" if on_next_month else "front-month"
            leg = _require_option_leg(
                next_month_lookup if on_next_month else option_lookup, dt, contract
            )
            if (
                one_minute_closes is not None
                and not on_next_month
                and abs(leg[0] + leg[1] - bar.close) > 1e-9
            ):
                prior_leg = option_lookup(dt - timedelta(seconds=1))
                if prior_leg is not None and abs(prior_leg[0] + prior_leg[1] - bar.close) <= 1e-9:
                    leg = prior_leg
            synthetic_price = leg[0] + leg[1]
            synthetic_fills = []
            for fill in bar.fills:
                side, raw_price, quantity = fill.side, fill.price, fill.qty
                delta = side * quantity
                closing = position != 0 and sign(delta) == -sign(position)
                slippage, fee, breakup = _fill_cost(
                    cost_model, side, leg, quantity, lot_size
                )
                price = raw_price + synthetic_price - bar.close + side * slippage
                synthetic_fills.append(Fill(side, price, quantity, fee, fill.tag))
                trade_log.append({
                    "timestamp": dt,
                    "action": fill.tag or ("CLOSE" if closing else "OPEN"),
                    "futures_side": "BUY" if side > 0 else "SELL",
                    "option_legs": "BUY CE, SELL PE" if side > 0 else "SELL CE, BUY PE",
                    "qty": quantity,
                    "price": price,
                    "strike_price": leg[1],
                    "fee": fee,
                    "contract": "next_month" if on_next_month else "expiring",
                    "is_rollover": False,
                    "cost_breakup": breakup,
                })
                position += delta
            output.append(Bar(dt, synthetic_price, tuple(synthetic_fills)))
            futures_closes.append(synthetic_price)

        if is_expiry_day and not contract_flipped:
            roll(bar_date)

    return output, futures_closes, roll_events, trade_log


def buy_and_hold(bars, reference_closes, initial, roll_events):
    roll_reentries = {
        event["date"]
        for event in roll_events
        if event["action"] in ("rollover", "contract_switch")
    }
    for bar, price in zip(bars, reference_closes):
        if not isfinite(price) or price <= 0:
            raise ValueError(f"non-positive benchmark price at {bar.dt:%Y-%m-%d %H:%M}")
    equity = [initial]
    for index in range(1, len(bars)):
        daily_return = (
            0.0
            if bars[index].dt in roll_reentries
            else reference_closes[index] / reference_closes[index - 1] - 1
        )
        equity.append(equity[-1] * (1 + daily_return))
    return equity


def _price_probe_names(source_bars):
    lows = [bar.low for bar in source_bars if bar.low is not None]
    highs = [bar.high for bar in source_bars if bar.high is not None]
    if not lows or not highs:
        return set()
    lower, upper = min(lows), max(highs)
    padding = 0.1 * (upper - lower)
    names = {name for bar in source_bars for name in bar.probes}
    selected = set()
    for name in names:
        values = [
            bar.probes[name]
            for bar in source_bars
            if name in bar.probes and isfinite(bar.probes[name])
        ]
        if values and min(values) >= lower - padding and max(values) <= upper + padding:
            selected.add(name)
    return selected


def _minute_ohlc(prices, dt, fallback):
    start = dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)
    if start == dt:
        start -= timedelta(minutes=30)
    i = bisect.bisect_right(prices, (start, float("inf")))
    j = bisect.bisect_right(prices, (dt, float("inf")))
    values = [price for stamp, price in prices[i:j] if stamp.date() == dt.date()]
    if not values:
        return fallback, fallback, fallback
    return values[0], max(values), min(values)


def _rollover_tags(roll_events):
    tags = {}
    for event in roll_events:
        action = event["action"]
        if action not in ("square_off", "rollover"):
            continue
        group = "Long" if event["side"] > 0 else "Short"
        role = "exit" if action == "square_off" else "entry"
        tags[event["date"]] = f"{group} rollover {role}"
    return tags


def make_chart_bars(source_bars, synthetic_bars, roll_events, month2_minutes):
    """Attach source OHLC/probes and named rollover orders to mapped bars."""
    from collections import defaultdict, deque

    sources = defaultdict(deque)
    for bar in source_bars:
        sources[bar.dt].append(bar)

    price_probes = _price_probe_names(source_bars)
    rollover_tags = _rollover_tags(roll_events)
    month2_starts = {
        event["date"].date(): event["date"]
        for event in roll_events
        if event["action"] in ("rollover", "contract_switch")
    }
    chart_bars = []

    for bar in synthetic_bars:
        source = sources[bar.dt].popleft() if sources[bar.dt] else None
        fills = bar.fills
        if bar.dt in rollover_tags:
            tag = rollover_tags[bar.dt]
            fills = tuple(
                replace(fill, tag=tag) if not fill.tag else fill for fill in fills
            )
        if source is None:
            chart_bars.append(replace(
                bar,
                fills=fills,
                open=bar.close,
                high=bar.close,
                low=bar.close,
                probes={},
            ))
            continue

        delta = bar.close - source.close
        month2_start = month2_starts.get(bar.dt.date())
        on_month2 = month2_start is not None and bar.dt >= month2_start
        if on_month2:
            open_, high, low = _minute_ohlc(month2_minutes, bar.dt, bar.close)
        else:
            open_ = source.open + delta
            high = source.high + delta
            low = source.low + delta
        high = max(high, open_, bar.close)
        low = min(low, open_, bar.close)
        probes = dict(source.probes)
        if on_month2:
            for name in price_probes:
                value = probes.get(name, float("nan"))
                probes[name] = value + delta if isfinite(value) else float("nan")
        chart_bars.append(replace(
            bar,
            fills=fills,
            open=open_,
            high=high,
            low=low,
            probes=probes,
        ))

    unique_bars = []
    for bar in chart_bars:
        if unique_bars and bar.dt <= unique_bars[-1].dt:
            bar = replace(bar, dt=unique_bars[-1].dt + timedelta(seconds=1))
        unique_bars.append(bar)
    return unique_bars
