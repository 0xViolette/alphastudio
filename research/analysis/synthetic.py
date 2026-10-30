"""Option-derived synthetic execution shared by both synthetic tearsheets."""

import argparse
import bisect
import calendar
import csv
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from math import isfinite

import costs as cost_cli
from cli import (
    add_report_arguments,
    emit_report,
    normalize_dates,
    prompt_date_window,
    prompt_output_files,
    resolve_initial,
    resolve_strategy,
    sync_cost_dates,
)
from config import ROOT
from engine import Bar, Fill, STRATEGIES, parse_stream, run_backtest
from ledger import sgn
from performance import analyze
from trade_log import write_synthetic_trade_log

FUTURES_CSV = ROOT / "data" / "raw" / "new_data.csv"
NIFTY_CSV = ROOT / "data" / "raw" / "nifty_atm_month1.csv"
NIFTY_MONTH2_CSV = ROOT / "data" / "raw" / "nifty_atm_month2.csv"
FUTURES_EMITTER = ROOT / "research" / "emitters" / "ohlc_csv_emitter.py"
OPTIONS_EMITTER = ROOT / "research" / "emitters" / "synthetic_ohlc_emitter.py"

EXPIRY_RULES = (
    (date(2000, 1, 1), 3),
    (date(2025, 9, 1), 1),
)
ROLLOVER_CUTOFF = time(15, 20)
BUY, SELL = +1, -1


def cost_schema():
    base = cost_cli.schema()
    meta = dict(base)
    presets = []
    for preset in base["presets"]:
        if preset["name"] == "none":
            presets.append(dict(preset))
        elif preset["name"] == "synthetic_futures":
            preset = dict(preset)
            preset["name"] = "costs"
            preset["description"] = "option costs (brokerage, txn, SEBI, STT, stamp, GST)"
            presets.append(preset)
    meta["presets"] = presets
    meta["default_preset"] = "costs"
    knobs = []
    for original in base["knobs"]:
        if original["key"] not in ("fee_per_order", "opt_slip_ticks", "opt_slip_pct"):
            continue
        knob = dict(original)
        knob["applies_to"] = [
            "costs" if value == "synthetic_futures" else value
            for value in knob.get("applies_to", [])
        ]
        if any(value in knob["applies_to"] for value in ("none", "costs")):
            knobs.append(knob)
    meta["knobs"] = knobs
    return meta


def _weekday_rule(day):
    weekday = EXPIRY_RULES[0][1]
    for effective, candidate in EXPIRY_RULES:
        if day >= effective:
            weekday = candidate
    return weekday


def nominal_monthly_expiry(year, month, at):
    weekday = _weekday_rule(at)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def build_expiry_calendar(trading_dates, start, end):
    """Monthly expiries, adjusted only when later sessions prove a closure."""
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
    """Per-leg option execution costs from the OCaml schema rates."""

    def __init__(self, meta, values=None):
        rates = meta["rates"]
        defaults = meta["defaults"]
        values = values or {}
        self.rates = rates
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
        return max(self.opt_tick_size, open_price + side * self.slippage(open_price))

    def fee(self, side, exec_price, lots, lot_size):
        return sum(self._leg_breakup(side, exec_price, lots, lot_size).values())

    def futures_fee(self, side, call_px, put_px, lots, lot_size):
        if side > 0:
            return (self.fee(BUY, call_px, lots, lot_size)
                    + self.fee(SELL, put_px, lots, lot_size))
        return (self.fee(SELL, call_px, lots, lot_size)
                + self.fee(BUY, put_px, lots, lot_size))

    def futures_cost(self, side, call_open, put_open, lots, lot_size):
        call_side = BUY if side > 0 else SELL
        put_side = -call_side
        call_px = self.execution_price(call_side, call_open)
        put_px = self.execution_price(put_side, put_open)
        slip_call = call_side * (call_px - call_open)
        slip_put = put_side * (put_px - put_open)
        return {
            "call_price": call_px,
            "put_price": put_px,
            "slippage": slip_call + slip_put,
            "fee": self.futures_fee(side, call_px, put_px, lots, lot_size),
            "breakup": self.futures_fee_breakup(
                side, call_px, put_px, lots, lot_size,
                raw_call_px=call_open, raw_put_px=put_open,
            ),
        }

    def futures_fee_breakup(
        self, side, call_px, put_px, lots, lot_size,
        raw_call_px=None, raw_put_px=None,
    ):
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
        return cost_cli.option_leg_breakup(
            side, exec_price, lots, lot_size, self.fee_per_order, self.rates
        )


def load_option_net(csv_path):
    """Load synchronized same-strike call/put closes from an option CSV."""
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
            dt = datetime.strptime(
                row[i_date].strip() + row[i_time].strip(), "%d/%m/%Y%H%M"
            )
            leg = row[i_type].strip().upper()
            entry = legs.setdefault(dt, {})
            entry[leg] = float(row[i_close])
            entry[f"{leg}_strike"] = float(row[i_strike])

    times, nets, strikes, calls, puts = [], [], [], [], []
    for dt in sorted(legs):
        value = legs[dt]
        if ("CALL" in value and "PUT" in value
                and value["CALL_strike"] == value["PUT_strike"]):
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
        i = bisect.bisect_right(times, dt) - 1
        if i < 0 or times[i].date() != dt.date():
            return None
        return nets[i], strikes[i], calls[i], puts[i]

    return lookup


def option_trading_dates(front_times, month2_times=()):
    return {dt.date() for dt in front_times} | {dt.date() for dt in month2_times}


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


def synthetic_close_series(times, nets, strikes):
    return [(dt, net + strike) for dt, net, strike in zip(times, nets, strikes)]


def first_stop_crossing(minute_bars, bar_end, side, stop_price):
    start = bar_end - timedelta(minutes=29)
    i = bisect.bisect_left(minute_bars, (start, float("-inf")))
    for dt, close in minute_bars[i:]:
        if dt > bar_end:
            break
        if ((side > 0 and close >= stop_price)
                or (side < 0 and close <= stop_price)):
            return dt
    return None


def _retime_expiry_stop_bars(bars, expiries, minute_bars):
    expiry_dates = set(expiries)
    minute_prices = dict(minute_bars)
    out = []
    for bar in bars:
        dt = bar.dt
        if dt.date() not in expiry_dates or dt.time() != time(15, 30) or not bar.fills:
            out.append(bar)
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
            out.append(Bar(fill_dt, reference, tuple(fills)))
        out.append(replace(bar, fills=tuple(remaining)))
    return out


def synthetic_bars_with_rollover(
    bars, opt_net, first_dt, last_dt, expiries, cost_model, lot_size,
    next_month_lookup=None, one_minute_closes=None,
):
    """Map signal fills to option-derived prices and roll at each expiry."""
    if one_minute_closes is not None:
        bars = _retime_expiry_stop_bars(bars, expiries, one_minute_closes)
    expiry_dates = set(expiries)
    by_date = {}
    for bar in bars:
        if not first_dt <= bar.dt <= last_dt:
            continue
        if (one_minute_closes is None and bar.dt.date() in expiry_dates
                and bar.dt.time() == time(15, 30) and bar.fills):
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

    out, fut_closes, roll_events, trade_log = [], [], [], []
    position = 0
    current_expiry_idx = None

    def roll(bar_date):
        nonlocal position, current_expiry_idx
        rollover_dt1 = datetime.combine(bar_date, ROLLOVER_CUTOFF)
        rollover_dt2 = rollover_dt1 + timedelta(minutes=1)
        leg1 = _require_option_leg(opt_net, rollover_dt1, "front-month")
        leg2 = _require_option_leg(next_month_lookup, rollover_dt2, "month2 reentry")
        price1, price2 = leg1[0] + leg1[1], leg2[0] + leg2[1]
        if position == 0:
            out.extend((
                Bar(rollover_dt1, price1),
                Bar(rollover_dt2, price2),
            ))
            fut_closes.extend((price1, price2))
            roll_events.append({
                "date": rollover_dt2, "action": "contract_switch",
                "qty": 0, "side": 0, "price": price2, "fee": 0.0,
            })
        else:
            qty = abs(position)
            close_side = -1 if position > 0 else 1
            slip1, fee1, breakup1 = _fill_cost(cost_model, close_side, leg1, qty, lot_size)
            fill_price1 = price1 + close_side * slip1
            out.append(Bar(
                rollover_dt1, price1,
                (Fill(close_side, fill_price1, qty, fee1),),
            ))
            fut_closes.append(price1)
            roll_events.append({
                "date": rollover_dt1, "action": "square_off",
                "qty": qty, "side": position, "price": fill_price1, "fee": fee1,
            })
            trade_log.append({
                "timestamp": rollover_dt1, "action": "ROLLOVER_SQUAREOFF",
                "futures_side": "SELL" if close_side < 0 else "BUY",
                "option_legs": "SELL CE, BUY PE" if close_side < 0 else "BUY CE, SELL PE",
                "qty": qty, "price": fill_price1, "strike_price": leg1[1], "fee": fee1,
                "contract": "expiring", "is_rollover": True,
                "cost_breakup": breakup1,
            })
            position = 0

            reentry_side = -close_side
            slip2, fee2, breakup2 = _fill_cost(
                cost_model, reentry_side, leg2, qty, lot_size
            )
            fill_price2 = price2 + reentry_side * slip2
            out.append(Bar(
                rollover_dt2, price2,
                (Fill(reentry_side, fill_price2, qty, fee2),),
            ))
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
                "qty": qty, "price": fill_price2, "strike_price": leg2[1], "fee": fee2,
                "contract": "next_month", "is_rollover": True,
                "cost_breakup": breakup2,
            })
            position = reentry_side * qty
        current_expiry_idx += 1
        if current_expiry_idx >= len(expiries):
            current_expiry_idx = None

    for bar_date in sorted(by_date):
        day_bars = sorted(by_date[bar_date], key=lambda bar: bar.dt)
        if current_expiry_idx is None:
            for i, expiry in enumerate(expiries):
                if expiry >= bar_date:
                    current_expiry_idx = i
                    break
        is_expiry_day = (
            current_expiry_idx is not None
            and bar_date == expiries[current_expiry_idx]
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
                next_month_lookup if on_next_month else opt_net, dt, contract
            )
            if (one_minute_closes is not None and not on_next_month
                    and abs(leg[0] + leg[1] - bar.close) > 1e-9):
                prior_leg = opt_net(dt - timedelta(seconds=1))
                if (prior_leg is not None
                        and abs(prior_leg[0] + prior_leg[1] - bar.close) <= 1e-9):
                    leg = prior_leg
            synthetic_px = leg[0] + leg[1]
            synthetic_fills = []
            for fill in bar.fills:
                side, raw_px, qty = fill.side, fill.price, fill.qty
                delta = side * qty
                closing = position != 0 and sgn(delta) == -sgn(position)
                slippage, fee, breakup = _fill_cost(cost_model, side, leg, qty, lot_size)
                price = raw_px + synthetic_px - bar.close + side * slippage
                synthetic_fills.append(Fill(side, price, qty, fee, fill.tag))
                trade_log.append({
                    "timestamp": dt,
                    "action": fill.tag or ("CLOSE" if closing else "OPEN"),
                    "futures_side": "BUY" if side > 0 else "SELL",
                    "option_legs": "BUY CE, SELL PE" if side > 0 else "SELL CE, BUY PE",
                    "qty": qty, "price": price, "strike_price": leg[1], "fee": fee,
                    "contract": "next_month" if on_next_month else "expiring",
                    "is_rollover": False,
                    "cost_breakup": breakup,
                })
                position += delta
            out.append(Bar(dt, synthetic_px, tuple(synthetic_fills)))
            fut_closes.append(synthetic_px)

        if is_expiry_day and not contract_flipped:
            roll(bar_date)

    return out, fut_closes, roll_events, trade_log


def buy_and_hold(bars, reference_closes, initial, roll_events):
    roll_reentries = {
        event["date"] for event in roll_events
        if event["action"] in ("rollover", "contract_switch")
    }
    for bar, price in zip(bars, reference_closes):
        if not isfinite(price) or price <= 0:
            raise ValueError(f"non-positive benchmark price at {bar.dt:%Y-%m-%d %H:%M}")
    equity = [initial]
    for i in range(1, len(bars)):
        daily_return = (
            0.0 if bars[i].dt in roll_reentries
            else reference_closes[i] / reference_closes[i - 1] - 1
        )
        equity.append(equity[-1] * (1 + daily_return))
    return equity


def main(
    source,
    *,
    options_emitter=OPTIONS_EMITTER,
    stem="synthetic_tearsheet",
    script_label=None,
):
    if source not in ("futures", "options"):
        raise ValueError(source)
    meta = cost_schema()
    data_path = FUTURES_CSV if source == "futures" else NIFTY_CSV
    parser = argparse.ArgumentParser(description=__doc__)
    add_report_arguments(parser, stem=stem, meta=meta, data_path=data_path)
    args = parser.parse_args()
    if args.trials < 0:
        sys.exit("--trials must be non-negative")
    normalize_dates(args)
    strategy = resolve_strategy(args.strategy)

    times_opt, nets_opt, strikes_opt, calls_opt, puts_opt = load_option_net(NIFTY_CSV)
    if not times_opt:
        sys.exit(f"no tradeable option minutes in {NIFTY_CSV}")
    opt_net = make_option_lookup(times_opt, nets_opt, strikes_opt, calls_opt, puts_opt)
    nm_times, nm_nets, nm_strikes, nm_calls, nm_puts = load_option_net(NIFTY_MONTH2_CSV)
    if not nm_times:
        sys.exit(f"no tradeable month2 option minutes in {NIFTY_MONTH2_CSV}")
    next_month_lookup = make_option_lookup(
        nm_times, nm_nets, nm_strikes, nm_calls, nm_puts
    )
    month2_till = nm_times[-1].date()
    if source == "futures":
        print(f"Next month (month2 CSV) data loaded: {len(nm_times)} tradeable minutes")

    opt_start, opt_till = times_opt[0].date(), times_opt[-1].date()
    till_cap = min(opt_till, month2_till)
    if source == "futures":
        data_till = cost_cli.data_bounds(FUTURES_CSV)[1]
        if data_till is not None:
            till_cap = min(till_cap, data_till)
    if args.date_from is None or args.date_from < opt_start:
        args.date_from = opt_start
    if args.date_till is None or args.date_till > till_cap:
        args.date_till = till_cap
    prompt_date_window(args)
    if args.date_from is not None and args.date_from < opt_start:
        args.date_from = opt_start
    if args.date_till is not None and args.date_till > till_cap:
        args.date_till = till_cap
    sync_cost_dates(args)

    cfg = cost_cli.resolve(args, ask_period=False, meta=meta, data_path=data_path)
    prompt_output_files(args, stem=stem)
    cfg, initial_source = resolve_initial(args, cfg)
    lot_size, initial = cfg.lot_size, cfg.initial_balance
    argv = cfg.argv()
    argv[argv.index("--costs") + 1] = "none"
    emitter = FUTURES_EMITTER if source == "futures" else options_emitter
    out, err = run_backtest(STRATEGIES[strategy], argv, emitter=emitter)
    try:
        bars = parse_stream(out).bars
    except ValueError as exc:
        sys.exit(str(exc))
    if not any(bar.fills for bar in bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    trading_dates = option_trading_dates(times_opt, nm_times)
    expiries = build_expiry_calendar(
        trading_dates, times_opt[0].date(), max(times_opt[-1].date(), month2_till)
    )
    if source == "futures":
        print(f"\nNIFTY expiries in range: {expiries[0]} to {expiries[-1]} "
              f"({len(expiries)} expiries)")
        print("Rollover rule: square off at 15:20 on expiry day, "
              "re-enter next month at 15:21\n")
    cost_model = None if cfg.preset == "none" else OptionCostModel(meta, cfg.values)
    one_minute = (
        synthetic_close_series(times_opt, nets_opt, strikes_opt)
        if source == "options" else None
    )
    synth_bars, reference_closes, roll_events, trade_log = synthetic_bars_with_rollover(
        bars, opt_net, times_opt[0], times_opt[-1], expiries,
        cost_model, lot_size, next_month_lookup, one_minute,
    )
    if not synth_bars:
        sys.exit("no executable bars within option coverage")
    position_rolls = sum(event["action"] == "square_off" for event in roll_events)
    print(f"Contract switches: {len(roll_events)}; position rolls: {position_rolls}")
    print(f"Trade log entries: {len(trade_log)}")
    if args.csv is not None:
        write_synthetic_trade_log(args.csv, trade_log)
        print(f"trade log saved to: {args.csv}")

    benchmark = buy_and_hold(synth_bars, reference_closes, initial, roll_events)
    if source == "futures":
        data = (f"synthetic futures from {NIFTY_CSV.name} (option leg prices) + "
                "real futures signals from new_data.csv")
        cost_label = "synthetic futures + options costs + expiry rollover"
        script = "research/analysis/synthetic_tearsheet.py"
    else:
        data = f"option-derived synthetic futures from {NIFTY_CSV.name} (strike - put + call)"
        cost_label = "option-derived synthetic futures + options costs"
        script = script_label or "research/analysis/synthetic_tearsheet_options.py"
    try:
        result = analyze(
            synth_bars,
            lot_size=lot_size,
            initial=initial,
            strategy=strategy,
            data=data,
            cost_model=cost_label,
            rf=args.rf,
            mar=args.mar,
            trials=args.trials,
            initial_source=initial_source,
            benchmark_equity=benchmark,
        )
    except ValueError as exc:
        sys.exit(str(exc))
    emit_report(
        result,
        args,
        title=(f"{strategy} — synthetic futures with expiry rollover "
               "(option leg prices, square-off on expiry day, re-enter next month)"),
        equity_label="Synthetic-futures equity (mark-to-market, option leg prices)",
        benchmark_label=f"Buy & hold synthetic ({NIFTY_CSV.name})",
        script_label=script,
        include_costs=False,
    )
