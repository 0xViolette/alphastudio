"""CLI entry point for tearsheets."""

import argparse
import base64
import csv
import html
import io
import os
import sys
from dataclasses import replace
from datetime import date, datetime
from math import exp, isfinite, sqrt
from pathlib import Path

import costs as cost_cli
from config import ROOT, OUT_PNG
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
from backtest import TAGS, parse_tags, parse_bars, ledger
from report import build_report, print_report
from html_report import write_html_report
from trade_log import (
    _date_arg, _prompt_date_window, _prompt_output_files,
    _select_date_window, _generate_trade_log, _write_trade_log_csv,
)
from metrics import (
    moments, quantile, downside_deviation, cvar,
    sharpe_var, psr, min_track_record, expected_max_sharpe,
    ulcer_index, drawdowns, math_prod,
)


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
    ap.add_argument("--out", type=Path, default=OUT_PNG)
    ap.add_argument(
        "--initial",
        type=float,
        default=None,
        help="starting account balance for engine and report (default: cost config)",
    )
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
        const=ROOT / "reports" / "tearsheets" / "tearsheet_report.html",
        default=None,
        metavar="PATH",
        help="write a styled, self-contained HTML report instead of printing "
             "the statistics (default: tearsheet_report.html)",
    )
    ap.add_argument(
        "--csv",
        nargs="?",
        type=Path,
        const=ROOT / "reports" / "trade-logs" / "tearsheet_trades.csv",
        default=None,
        metavar="PATH",
        help="write a CSV trade log with timestamps, sides, prices, fees "
             "(default: reports/trade-logs/tearsheet_trades.csv)",
    )
    # costs.py already registers --from and --till. Reuse those actions rather
    # than registering the same option strings here (argparse rejects duplicates).
    cost_cli.add_arguments(ap)
    from_dest = ap._option_string_actions["--from"].dest
    till_dest = ap._option_string_actions["--till"].dest
    args = ap.parse_args()
    if args.trials < 0:
        sys.exit("--trials must be non-negative")

    # Keep a stable internal name for the report-window logic, regardless of
    # the attribute names chosen by costs.py for its CLI options.
    args.date_from = getattr(args, from_dest, None)
    args.date_till = getattr(args, till_dest, None)
    if isinstance(args.date_from, str):
        args.date_from = _date_arg(args.date_from)
    if isinstance(args.date_till, str):
        args.date_till = _date_arg(args.date_till)

    strategy = resolve_strategy(args.strategy)
    _prompt_date_window(args)
    # costs.py's resolver parses date arguments from ISO strings via
    # datetime.strptime(...). Its argparse type may already have converted
    # explicit CLI values to datetime.date objects, so normalize both explicit
    # and prompted values back to strings before handing them to the resolver.
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
    initial = cfg.initial_balance
    if not isfinite(initial) or initial <= 0:
        sys.exit("initial balance must be finite and positive")
    initial_source = (
        "command line" if args.initial is not None or args.initial_balance is not None
        else "cost configuration"
    )
    lot_size = cfg.lot_size

    data_name = "new_data.csv"

    out, err = run_backtest(STRATEGIES[strategy], cfg.argv())
    import backtest
    backtest.TAGS = parse_tags(out)
    all_bars = parse_bars(out)
    if not all_bars:
        sys.exit(f"no bars found in strategy output; stderr was:\n{err}")
    if not any(f for _, _, f in all_bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    # The engine runs a fresh backtest over the selected date window.
    selected_indices = _select_date_window(args.date_from, args.date_till, all_bars)
    all_equity, all_net_lots, all_realized_pnl, all_trades, _ = ledger(
        all_bars, lot_size, initial
    )
    first_i = selected_indices[0]
    # Anchor the report immediately before the first selected bar, so that
    # fills and price movement on that first bar are counted in the window.
    start_equity = all_equity[first_i - 1] if first_i > 0 else initial
    start_realized = all_realized_pnl[first_i - 1] if first_i > 0 else 0.0
    bars = [all_bars[i] for i in selected_indices]
    equity = [initial + all_equity[i] - start_equity for i in selected_indices]
    net_lots = [all_net_lots[i] for i in selected_indices]
    realized_pnl = [
        all_realized_pnl[i] - start_realized for i in selected_indices
    ]
    date_from = bars[0][0].date()
    date_till = bars[-1][0].date()
    trades = [
        t for t in all_trades
        if date_from <= t["entry"].date() and t["exit"].date() <= date_till
    ]

    # Generate trade log from selected bars
    trade_log = _generate_trade_log(bars, cfg.preset, cfg.values, lot_size)
    total_fees = sum(
        fee for _, _, fills in bars for _, _, _, fee in fills
    )
    if not trades:
        sys.exit(
            f"no complete round-trip trades within {date_from} to {date_till}; "
            "choose a wider date range"
        )

    dates = [dt for dt, _, _ in bars]
    closes = [c for _, c, _ in bars]
    n_bars = len(bars)

    # ---- daily series: last bar of each session
    day_index = {}
    for i, dt in enumerate(dates):
        day_index[dt.date()] = i
    days = sorted(day_index)
    day_equity = [equity[day_index[d]] for d in days]
    day_close = [closes[day_index[d]] for d in days]
    n_days = len(days)
    if n_days < 2:
        sys.exit("at least two sessions are required for an annualized tearsheet")
    for dt, value in zip(dates, equity):
        if not isfinite(value) or value <= 0:
            sys.exit(f"account equity became non-positive on {dt:%Y-%m-%d %H:%M}; "
                     "return-based statistics are undefined")
    rets = [day_equity[i] / day_equity[i - 1] - 1 for i in range(1, n_days)]
    # the first session opens flat at `initial`, so prepend its return too
    rets.insert(0, day_equity[0] / initial - 1)

    years = (days[-1] - days[0]).days / 365.25
    ppy = n_days / years  # observed sessions per year, not the nominal 252
    mean_r, sd_r, skew, kurt = moments(rets)
    ann_ret = mean_r * ppy
    ann_vol = sd_r * sqrt(ppy)
    rf, mar = args.rf, args.mar
    rf_r = rf / ppy
    mar_r = mar / ppy

    total = equity[-1] / initial - 1
    cagr = (equity[-1] / initial) ** (1 / years) - 1

    # ---- drawdowns on the mark-to-market curve
    dd, peaks, episodes = drawdowns(equity, initial)
    dd_pct = [d / p * 100 for d, p in zip(dd, peaks)]
    worst_p = min(range(n_bars), key=lambda i: dd_pct[i])
    worst_ep = min(episodes,
                   key=lambda e: e["depth"] / peaks[e["trough_i"]],
                   default=None)
    depths = [e["depth"] for e in episodes]
    depths_pct = [e["depth"] / peaks[e["trough_i"]] * 100 for e in episodes]

    def ep_days(e):
        """Calendar length of a drawdown episode, open episodes to today."""
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

    # ---- risk-adjusted
    downside = downside_deviation(rets, mar_r)
    omega_num = sum(max(r - mar_r, 0.0) for r in rets)
    omega_den = sum(max(mar_r - r, 0.0) for r in rets)
    ulcer = ulcer_index(dd_pct)
    maxdd_pct_dec = dd_pct[worst_p] / 100

    sharpe = (mean_r - rf_r) / sd_r * sqrt(ppy) if sd_r else float("nan")
    sortino = (mean_r - mar_r) / downside * sqrt(ppy) if downside else float("nan")
    calmar = cagr / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan")

    # ---- trade statistics
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
        "payoff": (gross_win / len(wins)) / (gross_loss / len(losses))
        if wins and losses and gross_loss else 0.0,
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "max_win": best_t["pnl"] if best_t else None,
        "max_loss": worst_t["pnl"] if worst_t else None,
        "max_win_streak": streaks(lambda p: p > 0),
        "max_loss_streak": streaks(lambda p: p <= 0),
        "avg_hold_days": sum(
            (t["exit"] - t["entry"]).total_seconds() / 86400 for t in trades
        ) / len(trades),
        "avg_hold_bars": sum(t["bars"] for t in trades) / len(trades),
    }

    # ---- monthly returns, and the buy & hold reference
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
    annual = {y: math_prod(1 + r for r in rs) - 1 for y, rs in annual.items()}

    # Signed point P/L per closed trade, grouped by exit month.
    monthly_points, annual_points = {}, {}
    for t in trades:
        key = (t["exit"].year, t["exit"].month)
        monthly_points[key] = monthly_points.get(key, 0.0) + t.get("points", 0.0)
    for key, val in monthly_points.items():
        annual_points[key[0]] = annual_points.get(key[0], 0.0) + val

    bh_equity = [initial * c / closes[0] for c in day_close]
    bh_bars = [initial * c / closes[0] for c in closes]  # bar-frequency, for the plot
    bh_dd, bh_peaks, _ = drawdowns(bh_equity, initial)
    bh_rets = [bh_equity[i] / bh_equity[i - 1] - 1 for i in range(1, n_days)]
    bh_rets.insert(0, bh_equity[0] / initial - 1)
    b_mean, b_sd, _, _ = moments(bh_rets)
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
        a_mean, a_sd, _, _ = moments(active)
        cov = sum((a - mean_r) * (b - b_mean) for a, b in zip(rets, bh_rets)) / (n_days - 1)
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
            "up_cap": (sum(a for a, _ in up) / len(up)) /
                      (sum(b for _, b in up) / len(up))
                      if up and sum(b for _, b in up) else float("nan"),
            "down_cap": (sum(a for a, _ in dn) / len(dn)) /
                        (sum(b for _, b in dn) / len(dn))
                        if dn and sum(b for _, b in dn) else float("nan"),
        })

    sr_period = (mean_r - rf_r) / sd_r if sd_r else float("nan")
    psr0 = psr(n_days, sr_period, skew, kurt) if sd_r else float("nan")
    # MinTRL answers "how long until the Sharpe is *significantly* above 1.0?"
    # (per-period equivalent, since PSR works in per-period units)
    sr_target = 1.0 / sqrt(ppy)
    mintrl = (
        0.0 if sd_r and psr(n_days, sr_period, skew, kurt, sr_target) >= 0.95
        else min_track_record(sr_period, sr_target, skew, kurt)
        if sd_r else float("nan")
    )
    var_sharpe = sharpe_var(n_days, sr_period, skew, kurt) * ppy if sd_r else 0.0
    emax = expected_max_sharpe(args.trials, var_sharpe)
    # PSR wants per-period units, so the deflation benchmark converts too
    dsr = (
        psr(n_days, sr_period, skew, kurt, sr_star=emax / sqrt(ppy))
        if sd_r and args.trials > 0 else float("nan")
    )

    top5_eps = sorted(
        episodes, key=lambda e: e["depth"] / peaks[e["trough_i"]])[:5]

    kw = dict(
        strategy=strategy, data=data_name,
        n_bars=n_bars, n_days=n_days, n_trades=len(trades),
        d0=days[0], d1=days[-1], years=years,
        initial=initial, final=equity[-1], pnl=equity[-1] - initial, total=total,
        final_realized=initial + realized_pnl[-1],
        final_unrealized=equity[-1] - initial - realized_pnl[-1],
        cagr=cagr, ann_ret=ann_ret, ann_vol=ann_vol, ppy=ppy,
        best_day_r=max(rets), best_day=days[max(range(n_days), key=lambda i: rets[i])],
        worst_day_r=min(rets), worst_day=days[min(range(n_days), key=lambda i: rets[i])],
        pos_days=sum(1 for r in rets if r > 0), monthly=monthly,
        rf=rf, mar=mar, sharpe=sharpe, sortino=sortino, calmar=calmar,
        omega=omega_num / omega_den if omega_den else float("nan"),
        ulcer=ulcer, upi=ann_ret * 100 / ulcer if ulcer else float("nan"),
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
        underwater_pct=underwater_days / span_days,
        underwater_days=underwater_days, span_days=span_days,
        top5=[e["depth"] for e in top5_eps],
        peak_at_episode=[peaks[e["trough_i"]] for e in top5_eps],
        trades=tstats,
        max_win_date=best_t["exit"] if best_t else None,
        max_loss_date=worst_t["exit"] if worst_t else None,
        total_fees=total_fees, gross_win=gross_win,
        cost_model=cfg.label(), initial_source=initial_source,
        exposure=sum(1 for q in net_lots if not is_zero(q)) / n_bars,
        var95=quantile(rets, 0.05), var99=quantile(rets, 0.01),
        cvar95=cvar(rets, 0.05), cvar99=cvar(rets, 0.01),
        skew=skew, kurt=kurt,
        tail=abs(quantile(rets, 0.95)) / abs(quantile(rets, 0.05))
        if quantile(rets, 0.05) else float("nan"),
        pos_months=sum(1 for r in monthly.values() if r > 0),
        best_m=max(monthly.values()), worst_m=min(monthly.values()),
        psr=psr0, mintrl=mintrl, trials=args.trials, emax=emax, dsr=dsr,
        annual=annual, bench=bench,
        monthly_points=monthly_points, annual_points=annual_points,
    )
    content = build_report(kw)
    if args.export is None:
        print_report(content)

    # Write CSV trade log if requested
    if args.csv is not None:
        import csv as csv_mod
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        _write_trade_log_csv(args.csv, trade_log, cfg.preset, cfg.values, lot_size)
        print(f"trade log saved to: {args.csv}")

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

    ax1.plot(dates, equity, color="tab:blue", lw=1.3, label="Account equity (mark-to-market)")
    ax1.plot(dates, bh_bars, color="0.45", lw=1.0, ls="-.",
             label=f"Buy & hold {data_name}")
    ax1.plot(dates, peaks, color="0.55", lw=1.0, ls="--", label="Equity high-water mark")
    ax1.fill_between(dates, equity, peaks, color="tab:red", alpha=0.30,
                     label="Drawdown (underwater)")
    ax1.fill_between(dates, equity, initial, where=[v >= initial for v in equity],
                     color="tab:green", alpha=0.10, label="Above starting balance")
    ax1.axhline(initial, color="0.4", lw=0.9, ls=":")
    ax1.annotate(f"start {initial:,.0f}", (dates[0], initial),
                 textcoords="offset points", xytext=(4, -14), fontsize=8, color="0.35")
    ax1.plot([dates[worst_p]], [equity[worst_p]], "o", color="tab:red", ms=6, zorder=5)
    annotation_side = 1 if worst_p <= n_bars // 2 else -1
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
        f"{strategy} on {data_name} \u2014 equity, benchmark and drawdowns",
        fontsize=14, pad=12,
    )
    ax1.set_ylabel("Account equity")
    ax1.yaxis.set_major_formatter(money)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=9)

    avg_dd_pct = sum(depths_pct) / len(depths_pct) if depths_pct else 0.0
    ax2.fill_between(dates, 0, dd_pct, color="tab:red", alpha=0.45)
    ax2.plot(dates, dd_pct, color="tab:red", lw=1.1)
    ax2.axhline(avg_dd_pct, color="0.35", lw=1.0, ls="--",
                label=f"average drawdown {avg_dd_pct:.2f}%")
    ax2.plot([dates[worst_p]], [dd_pct[worst_p]], "o", color="darkred", ms=6, zorder=5)
    ax2.annotate(
        f"max {dd_pct[worst_p]:.2f}%  ({dd[worst_p]:,.0f})\n{dates[worst_p]:%d %b %Y}",
        (dates[worst_p], dd_pct[worst_p]), textcoords="offset points",
        xytext=(14, -6), fontsize=9, color="darkred", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="darkred", lw=1.0),
    )
    ax2.legend(loc="lower left", framealpha=0.9, fontsize=9)
    ax2.set_title("Underwater curve \u2014 drawdown from the equity high-water mark",
                  fontsize=12, pad=8)
    ax2.set_ylabel("Drawdown from peak (%)")
    ax2.set_ylim(min(dd_pct) * 1.25, 1.0)
    ax2.grid(True, alpha=0.3)

    window = 126
    roll = []
    for i in range(window - 1, n_days):
        w = rets[i - window + 1:i + 1]
        m, s, _, _ = moments(w)
        roll.append((days[i], (m - rf_r) / s * sqrt(ppy) if s else float("nan")))
    ax3.plot([d for d, _ in roll], [v for _, v in roll], color="tab:purple", lw=1.1,
             label=f"rolling {window}-session Sharpe")
    ax3.axhline(sharpe, color="0.35", lw=1.0, ls="--", label=f"full-period Sharpe {sharpe:.2f}")
    ax3.axhline(0.0, color="0.7", lw=0.8, ls=":")
    ax3.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax3.set_title("Rolling Sharpe ratio \u2014 is the edge persistent or a spike?",
                  fontsize=12, pad=8)
    ax3.set_ylabel("Sharpe (annualized)")
    ax3.set_xlabel("Date")
    ax3.grid(True, alpha=0.3)

    ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax3.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))

    fig.text(
        0.99, 0.005,
        f"CAGR {cagr:+.2%}  |  Sharpe {sharpe:.2f}  |  Sortino {sortino:.2f}  |  "
        f"Calmar {calmar:.2f}  |  max DD {dd_pct[worst_p]:.2f}%  |  "
        f"underwater {underwater_days / span_days:.0%}"
        f"  |  script: research/analysis/tearsheet.py",
        ha="right", fontsize=8.5, color="0.35",
    )
    fig.subplots_adjust(hspace=0.14, left=0.085, right=0.97, top=0.955, bottom=0.06)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150, facecolor="white")
    print(f"plot saved to:  {args.out}")

    if args.export is not None:
        write_html_report(args.export, kw, content, args.out.read_bytes())
        print(f"report saved to: {args.export}")


if __name__ == "__main__":
    main()
