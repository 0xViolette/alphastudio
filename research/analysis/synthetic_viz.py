#!/usr/bin/env python3
"""Candlestick chart of a pure option-derived synthetic-futures backtest.

Uses the same interactive Lightweight Charts visualization and optional Plotly
PNG as viz.py. Both strategy signals and chart prices come from synthetic
futures (same-strike call - put + strike), with expiry rollover and option-leg
execution costs as in synthetic_tearsheet_options.py; new_data.csv is not used.

Usage from research/analysis:
    uv run python synthetic_viz.py --strategy chandelier
    uv run python synthetic_viz.py --from 2024-01-01 --till 2024-12-31
    uv run python synthetic_viz.py --costs none --png

--from/--till (YYYY-MM-DD, IST; --till inclusive) restrict the backtest. HTML
is self-contained and defaults to a dark theme with a Dark/Light toggle.
"""

import argparse
import bisect
import math
import sys
from collections import defaultdict, deque
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import costs as cost_cli
import synthetic
import viz
from cli import normalize_dates, prompt_date_window, resolve_strategy, sync_cost_dates
from config import ROOT
from engine import DEFAULT_STRATEGY, STRATEGIES, parse_stream, run_backtest
from ledger import replay


ROLL_ORDERS = {
    "Long rollover entry": ("Enter long · rollover", True, "long", +1),
    "Long rollover exit": ("Exit long · rollover", False, "long", -1),
    "Short rollover entry": ("Enter short · rollover", True, "short", -1),
    "Short rollover exit": ("Exit short · rollover", False, "short", +1),
}


def _register_rollover_orders():
    index = max((spec["index"] for spec in viz.ORDER_SPECS.values()), default=-1) + 1
    for tag, (action, entry, group, side) in ROLL_ORDERS.items():
        if tag not in viz.ORDER_SPECS:
            viz.ORDER_SPECS[tag] = {
                "tag": tag,
                "action": action,
                "label": action,
                "entry": entry,
                "group": group,
                "fill_side": side,
                "index": index,
                "stop": None,
            }
            index += 1
        viz.TAG_ACTION[tag] = action


_register_rollover_orders()


def clean_month2(front, month2):
    """Drop gross month2 data errors relative to the same-session front price."""
    front_lookup = synthetic.make_option_lookup(front[0], front[1], front[2])
    keep = []
    for i, dt in enumerate(month2[0]):
        reference = front_lookup(dt)
        price = month2[1][i] + month2[2][i]
        if reference is not None:
            front_price = reference[0] + reference[1]
            invalid = (
                not math.isfinite(price)
                or price <= 0
                or abs(price - front_price) > 0.20 * front_price
            )
            if invalid:
                continue
        keep.append(i)
    dropped = len(month2[0]) - len(keep)
    if dropped:
        print(
            f"warning: ignored {dropped:,} corrupt month2 option minutes "
            "with synthetic prices over 20% away from front-month prices",
            file=sys.stderr,
        )
    return tuple([values[i] for i in keep] for values in month2)


def load_options():
    front = synthetic.load_option_net(synthetic.NIFTY_CSV)
    if not front[0]:
        sys.exit(f"no tradeable option minutes in {synthetic.NIFTY_CSV}")
    month2 = synthetic.load_option_net(synthetic.NIFTY_MONTH2_CSV)
    month2 = clean_month2(front, month2)
    if not month2[0]:
        sys.exit(f"no tradeable month2 option minutes in {synthetic.NIFTY_MONTH2_CSV}")
    return front, month2


def resolve_window(args, front_times, month2_times):
    opt_start, opt_till = front_times[0].date(), front_times[-1].date()
    till_cap = min(opt_till, month2_times[-1].date())
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


def make_chart_bars(source_bars, synth_bars, roll_events, month2_minutes):
    """Attach synthetic OHLC/probes and named rollover orders to mapped bars."""
    sources = defaultdict(deque)
    for bar in source_bars:
        sources[bar.dt].append(bar)

    nan = float("nan")
    source_probes = {
        name: [bar.probes.get(name, nan) for bar in source_bars]
        for name in {name for bar in source_bars for name in bar.probes}
    }
    price_probe_names = set(viz.price_probes({
        "low": [bar.low for bar in source_bars],
        "high": [bar.high for bar in source_bars],
        "probes": source_probes,
    }))
    rollover_tags = _rollover_tags(roll_events)
    month2_starts = {
        event["date"].date(): event["date"]
        for event in roll_events
        if event["action"] in ("rollover", "contract_switch")
    }
    chart_bars = []

    for bar in synth_bars:
        source = sources[bar.dt].popleft() if sources[bar.dt] else None
        fills = bar.fills
        if bar.dt in rollover_tags:
            tag = rollover_tags[bar.dt]
            fills = tuple(
                replace(fill, tag=tag) if not fill.tag else fill
                for fill in fills
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
            for name in price_probe_names:
                value = probes.get(name, nan)
                probes[name] = value + delta if not math.isnan(value) else nan
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


def load_data(args, cfg, meta, front, month2):
    times_opt, nets_opt, strikes_opt, calls_opt, puts_opt = front
    nm_times, nm_nets, nm_strikes, nm_calls, nm_puts = month2
    opt_net = synthetic.make_option_lookup(
        times_opt, nets_opt, strikes_opt, calls_opt, puts_opt
    )
    next_month_lookup = synthetic.make_option_lookup(
        nm_times, nm_nets, nm_strikes, nm_calls, nm_puts
    )

    argv = cfg.argv()
    argv[argv.index("--costs") + 1] = "none"
    out, err = run_backtest(
        STRATEGIES[args.strategy], argv, emitter=synthetic.OPTIONS_EMITTER
    )
    try:
        stream = parse_stream(out, side_for=viz.fill_side)
    except ValueError as exc:
        sys.exit(str(exc))
    if not any(bar.fills for bar in stream.bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    expiries = synthetic.build_expiry_calendar(
        synthetic.option_trading_dates(times_opt, nm_times),
        times_opt[0].date(),
        max(times_opt[-1].date(), nm_times[-1].date()),
    )
    cost_model = None if cfg.preset == "none" else synthetic.OptionCostModel(meta, cfg.values)
    one_minute = synthetic.synthetic_close_series(times_opt, nets_opt, strikes_opt)
    synth_bars, _, roll_events, _ = synthetic.synthetic_bars_with_rollover(
        stream.bars,
        opt_net,
        times_opt[0],
        times_opt[-1],
        expiries,
        cost_model,
        cfg.lot_size,
        next_month_lookup,
        one_minute,
    )
    if not synth_bars:
        sys.exit("no executable bars within option coverage")

    month2_minutes = synthetic.synthetic_close_series(nm_times, nm_nets, nm_strikes)
    bars = make_chart_bars(stream.bars, synth_bars, roll_events, month2_minutes)
    account = replay(bars, cfg.lot_size, cfg.initial_balance)
    if not account.trades:
        print("warning: no round-trip trades found in the data", file=sys.stderr)

    nan = float("nan")
    probes = {
        name: [bar.probes.get(name, nan) for bar in bars]
        for name in stream.probe_names
    }
    position_rolls = sum(event["action"] == "square_off" for event in roll_events)
    print(f"Contract switches: {len(roll_events)}; position rolls: {position_rolls}")
    cost_label = "options costs" if cfg.preset != "none" else "no execution costs"
    return {
        "dates": [bar.dt for bar in bars],
        "open": [bar.open for bar in bars],
        "high": [bar.high for bar in bars],
        "low": [bar.low for bar in bars],
        "close": [bar.close for bar in bars],
        "probes": probes,
        "bar_fills": [
            [(f.side, f.price, f.qty, f.tag, f.fee) for f in bar.fills]
            for bar in bars
        ],
        "bars": bars,
        "account": account,
        "equity": account.equity,
        "net": account.net_lots,
        "realized": account.realized_pnl,
        "trades": account.trades,
        "initial": cfg.initial_balance,
        "lot_size": cfg.lot_size,
        "costs": f"option-derived synthetic futures + {cost_label}",
        "strategy": args.strategy,
        "data": f"{synthetic.NIFTY_CSV.name} + {synthetic.NIFTY_MONTH2_CSV.name}",
        "args": args,
    }


def main():
    meta = synthetic.cost_schema()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--strategy",
        choices=list(STRATEGIES),
        default=None,
        help=f"which strategy to run (default: {DEFAULT_STRATEGY}; prompts on a TTY when omitted)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="output path stem (default: synthetic_candles.html in project reports/plots)",
    )
    ap.add_argument("--png", action="store_true", help="also write a static PNG via kaleido")
    ap.add_argument(
        "--style",
        choices=list(viz.THEMES),
        default="dark",
        help="theme at render time (HTML also gets a Dark/Light toggle)",
    )
    cost_cli.add_arguments(ap, meta=meta, data_path=synthetic.NIFTY_CSV)
    args = ap.parse_args()
    normalize_dates(args)
    args.strategy = resolve_strategy(args.strategy)
    front, month2 = load_options()
    resolve_window(args, front[0], month2[0])
    cfg = cost_cli.resolve(
        args, ask_period=False, meta=meta, data_path=synthetic.NIFTY_CSV
    )
    ctx = load_data(args, cfg, meta, front, month2)
    out_base = args.out or (ROOT / "reports" / "plots" / "synthetic_candles")
    viz.render(ctx, args.style, out_base, args.png)


if __name__ == "__main__":
    main()
