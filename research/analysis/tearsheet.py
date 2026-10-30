"""Performance report and drawdown plots for a real-futures strategy."""

import argparse
import sys

import costs as cost_cli
from cli import (
    add_report_arguments,
    emit_report,
    normalize_dates,
    prompt_date_window,
    prompt_output_files,
    resolve_initial,
    resolve_strategy,
    select_date_window,
    sync_cost_dates,
)
from engine import EMITTER, STRATEGIES, parse_stream, run_backtest
from ledger import AccountSeries, replay
from performance import analyze
from trade_log import _generate_trade_log, _write_trade_log_csv

SCRIPT = "research/analysis/tearsheet.py"


def main(
    *,
    emitter=EMITTER,
    data_path=cost_cli.DATA_CSV,
    data_label="new_data.csv",
    stem="tearsheet",
    script_label=SCRIPT,
):
    parser = argparse.ArgumentParser(description=__doc__)
    add_report_arguments(parser, stem=stem)
    args = parser.parse_args()
    if args.trials < 0:
        sys.exit("--trials must be non-negative")

    normalize_dates(args)
    strategy = resolve_strategy(args.strategy)
    prompt_date_window(args)
    sync_cost_dates(args)
    cfg = cost_cli.resolve(args, ask_period=False, data_path=data_path)
    prompt_output_files(args, stem=stem)
    cfg, initial_source = resolve_initial(args, cfg)
    initial, lot_size = cfg.initial_balance, cfg.lot_size

    out, err = run_backtest(STRATEGIES[strategy], cfg.argv(), emitter=emitter)
    try:
        all_bars = parse_stream(out).bars
    except ValueError as exc:
        sys.exit(str(exc))
    if not all_bars:
        sys.exit(f"no bars found in strategy output; stderr was:\n{err}")
    if not any(bar.fills for bar in all_bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    selected = select_date_window(args.date_from, args.date_till, all_bars)
    full_account = replay(all_bars, lot_size, initial)
    first = selected[0]
    start_equity = full_account.equity[first - 1] if first > 0 else initial
    start_realized = full_account.realized_pnl[first - 1] if first > 0 else 0.0
    bars = [all_bars[i] for i in selected]
    equity = [initial + full_account.equity[i] - start_equity for i in selected]
    realized = [
        full_account.realized_pnl[i] - start_realized for i in selected
    ]
    date_from, date_till = bars[0].dt.date(), bars[-1].dt.date()
    trades = [
        trade for trade in full_account.trades
        if date_from <= trade.entry.date() and trade.exit.date() <= date_till
    ]
    account = AccountSeries(
        equity=equity,
        net_lots=[full_account.net_lots[i] for i in selected],
        realized_pnl=realized,
        trades=trades,
        fees=sum(fill.fee for bar in bars for fill in bar.fills),
    )
    if not trades:
        sys.exit(
            f"no complete round-trip trades within {date_from} to {date_till}; "
            "choose a wider date range"
        )

    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        entries = _generate_trade_log(bars, cfg.preset, cfg.values, lot_size)
        _write_trade_log_csv(args.csv, entries, cfg.preset, cfg.values, lot_size)
        print(f"trade log saved to: {args.csv}")

    benchmark = [initial * bar.close / bars[0].close for bar in bars]
    try:
        result = analyze(
            bars,
            account=account,
            lot_size=lot_size,
            initial=initial,
            strategy=strategy,
            data=data_label,
            cost_model=cfg.label(),
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
        title=f"{strategy} on {data_label} — equity, benchmark and drawdowns",
        equity_label="Account equity (mark-to-market)",
        benchmark_label=f"Buy & hold {data_label}",
        script_label=script_label,
    )


if __name__ == "__main__":
    main()
