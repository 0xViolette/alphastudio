"""Performance report + drawdown plots for a bin/ strategy.

Usage (from the strategy/ project root):
    python3 scripts/drawdown.py [meanrev|main] [--out drawdown.png]
        [--rf 0.065] [--mar 0.0] [--trials 100]
        [--export [report.html]]

By default the report is printed to the terminal. With --export (or
--export PATH) it is written to a self-contained, styled HTML file instead,
with the drawdown figure embedded, colour-coded metrics and a monthly
returns heatmap.

Runs the same pipeline as scripts/plot_pnl.py (new_data_emitter.py | dune
exec <strategy>), replays the
printed fills through the lot-offsetting ledger of lib/ledger.ml, and reports
the standard performance statistics of the trading literature on a
mark-to-market equity curve (initial_balance read from lib/backtest.ml, with
every open lot marked to each bar's close, so percentage drawdowns and daily
returns are relative to the account rather than to a zero-based P/L series).

The terminal report is one aligned block; the figure has three panels:
equity vs. buy & hold, the underwater drawdown curve, and a rolling 126-day
Sharpe ratio. `--trials N` additionally deflates the Sharpe ratio for the
fact that N strategy variants were tried (Bailey & Lopez de Prado 2014).
"""

import argparse
import base64
import csv
import html
import io
import os
import sys
from datetime import datetime
from math import exp, sqrt
from pathlib import Path
from statistics import NormalDist

from plot_pnl import (
    DUNE_TARGETS,
    EMITTER,
    SIDE_BY_TAG,
    cost_model_name,
    is_zero,
    read_ocaml_literal,
    run_backtest,
    sgn,
)

ROOT = Path(__file__).resolve().parent.parent
OUT_PNG = ROOT / "drawdown.png"

W = 84  # report width, in terminal columns
NORMAL = NormalDist()
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def tone(value, fmt):
    """Format value, green if positive, red if negative."""
    s = fmt.format(value)
    if not _COLOR or value is None:
        return s
    if value > 0:
        return f"\033[32m{s}\033[0m"
    if value < 0:
        return f"\033[31m{s}\033[0m"
    return s


def dim(s):
    return f"\033[2m{s}\033[0m" if _COLOR else s


def bold(s):
    return f"\033[1m{s}\033[0m" if _COLOR else s


def rule(title=""):
    head = f"\u2500\u2500 {title} " if title else ""
    print(bold(head) + "\u2500" * max(0, W - len(head)))


def kv(label, value, note=""):
    # pad before dimming, otherwise the escape codes count toward the width
    print(f"  {dim(f'{label:<32}')}{value:>18}   {dim(note)}")


def banner(title, subtitle=""):
    inner = W - 2
    bar = "\u2500" * inner
    if _COLOR:
        bar = f"\033[36m{bar}\033[0m"
    print(f"\u256d{bar}\u256e")
    print(f"\u2502 {bold(title)}")
    if subtitle:
        print(f"\u2502   {dim(subtitle)}")
    print(f"\u2570{bar}\u256e")


# ---------------------------------------------------------------- bar stream


def parse_bars(out):
    """Every bar of the backtest's CSV stream as (datetime, close, fills)."""
    rows = list(csv.reader(io.StringIO(out)))
    if not rows:
        return []
    header = rows[0]
    i_dt, i_tm = header.index("Date"), header.index("Time")
    i_cl = header.index("Close")
    i_evt, i_px, i_qt = (header.index(c) for c in ("event", "fill_price", "fill_qty"))
    i_ct = header.index("fill_cost") if "fill_cost" in header else None
    bars = []
    for row in rows[1:]:
        if not row:
            continue
        dt = datetime.strptime(row[i_dt] + " " + row[i_tm], "%m/%d/%y %I:%M %p")
        fills = []
        events = [e.strip() for e in row[i_evt].split("|") if e.strip().endswith("filled")]
        if events:
            prices, qtys = row[i_px].split("|"), row[i_qt].split("|")
            costs = row[i_ct].split("|") if i_ct is not None else ["0"] * len(events)
            for event, price, qty, cost in zip(events, prices, qtys, costs):
                tag = event[: -len("filled")].strip()
                if tag not in SIDE_BY_TAG:
                    sys.exit(
                        f"unknown order tag {tag!r} in {event!r}; "
                        f"add it to SIDE_BY_TAG (direction of the fill)"
                    )
                fills.append((SIDE_BY_TAG[tag], float(price), float(qty),
                              float(cost or 0)))
        bars.append((dt, float(row[i_cl]), fills))
    return bars


def ledger(bars, lot_size, initial):
    """One pass over the bars -> (equity, net_lots, realized_pnl, trades, fees).

    Equity is marked to market: initial balance + realized P/L + the P/L of
    every still-open lot at the bar's close. Trades are the FIFO round trips
    the lot ledger closes, net of entry and exit fees. `realized_pnl` is
    realized-only and net: each fill's fee is subtracted as it happens.
    Slippage needs no handling — it is already inside the fill prices.
    """
    realized, lots, fees = 0.0, [], 0.0
    equity, net, realized_pnl, trades = [], [], [], []
    for i, (dt, close, fills) in enumerate(bars):
        for side, price, qty, fee in fills:
            realized -= fee
            fees += fee
            this_fpl = fee / qty if qty else 0.0
            remaining = side * qty
            while lots and not is_zero(remaining) and sgn(lots[0][1]) == -sgn(remaining):
                entry_price, lot_qty, entry_dt, entry_i, entry_fpl = lots[0]
                rest = lots[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                gross = direction * closed * lot_size * (price - entry_price)
                realized += gross
                trades.append(
                    {
                        "pnl": gross - closed * (entry_fpl + this_fpl),
                        "lots": closed,
                        "dir": "long" if direction > 0 else "short",
                        "entry": entry_dt,
                        "exit": dt,
                        "bars": i - entry_i,
                    }
                )
                left = lot_qty - direction * closed
                if not is_zero(left):
                    rest = [(entry_price, left, entry_dt, entry_i, entry_fpl)] + rest
                lots = rest
                remaining += direction * closed
            if not is_zero(remaining):
                lots.append((price, remaining, dt, i, this_fpl))
        unrealized = sum(
            sgn(q) * abs(q) * lot_size * (close - price) for price, q, _, _, _ in lots
        )
        equity.append(initial + realized + unrealized)
        realized_pnl.append(realized)
        net.append(sum(q for _, q, _, _, _ in lots))
    return equity, net, realized_pnl, trades, fees


# ---------------------------------------------------------------- statistics


def moments(xs):
    """(mean, sample stdev, skewness, non-excess kurtosis)."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0, 3.0
    mean = sum(xs) / n
    dev = [x - mean for x in xs]
    m2 = sum(d * d for d in dev) / n
    m3 = sum(d**3 for d in dev) / n
    m4 = sum(d**4 for d in dev) / n
    sd = sqrt(sum(d * d for d in dev) / (n - 1))
    skew = m3 / m2**1.5 if m2 else 0.0
    kurt = m4 / m2**2 if m2 else 3.0
    return mean, sd, skew, kurt


def quantile(xs, q):
    """Linear-interpolation quantile of a sorted-able sample."""
    ys = sorted(xs)
    if not ys:
        return float("nan")
    pos = q * (len(ys) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def downside_deviation(rs, target=0.0):
    """Target downside deviation (Sortino): sqrt(mean of min(r - T, 0)^2)."""
    return sqrt(sum(min(r - target, 0.0) ** 2 for r in rs) / len(rs))


def cvar(rs, q):
    """Historical expected shortfall: mean of returns at or below quantile q."""
    cut = quantile(rs, q)
    tail = [r for r in rs if r <= cut]
    return sum(tail) / len(tail) if tail else cut


def sharpe_var(n, sr, g3, g4):
    """Variance of the estimated Sharpe ratio, Merton's result as used by
    Bailey & Lopez de Prado (2012): (1 - g3*SR + (g4-1)/4*SR^2) / (T-1)."""
    return (1 - g3 * sr + (g4 - 1) / 4 * sr**2) / (n - 1)


def psr(n, sr, g3, g4, sr_star=0.0):
    """Probabilistic Sharpe ratio: P(true SR > SR*), non-normality corrected."""
    return NORMAL.cdf((sr - sr_star) * sqrt(n - 1) / sqrt(sharpe_var(n, sr, g3, g4)))


def min_track_record(sr, sr_star, g3, g4, alpha=0.05):
    """MinTRL: periods needed for SR to beat SR* with 1 - alpha confidence."""
    var = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    return 1 + var * (NORMAL.inv_cdf(1 - alpha) / (sr - sr_star)) ** 2


def expected_max_sharpe(trials, var_sharpe):
    """E[max SR] over `trials` independent trials with the same variance."""
    if trials < 2:
        return 0.0
    euler = 0.5772156649
    g = (1 - euler) * NORMAL.inv_cdf(1 - 1 / trials)
    g += euler * NORMAL.inv_cdf(1 - 1 / (trials * exp(1) - 1))
    return sqrt(var_sharpe) * g


def ulcer_index(dd_pct):
    """Martin (1987): RMS of the percentage drawdown series."""
    return sqrt(sum(d * d for d in dd_pct) / len(dd_pct))


def drawdowns(equity):
    """Return (dd_abs, peak_abs, episodes) for a list of equity values."""
    dd, peaks, run_peak = [], [], float("-inf")
    for v in equity:
        run_peak = max(run_peak, v)
        peaks.append(run_peak)
        dd.append(v - run_peak)

    episodes, cur = [], None
    for i, (v, p) in enumerate(zip(equity, peaks)):
        at_peak = abs(v - p) < 1e-9
        if at_peak:
            if cur is not None:
                cur["recover"] = i
                episodes.append(cur)
                cur = None
        else:
            if cur is None:
                cur = {"peak_i": i - 1, "trough_i": i, "recover": None}
            if dd[i] < dd[cur["trough_i"]]:
                cur["trough_i"] = i
    if cur is not None:
        episodes.append(cur)
    for ep in episodes:
        ep["depth"] = dd[ep["trough_i"]]
    return dd, peaks, episodes


# ------------------------------------------------------------------ report


def polarity(value):
    """+1 / -1 for a signed metric, None when it is zero, NaN or text."""
    try:
        if value > 0:
            return 1
        if value < 0:
            return -1
    except TypeError:
        pass
    return None


def signed_text(s, sign):
    if not _COLOR or not sign:
        return s
    return f"\033[{32 if sign > 0 else 31}m{s}\033[0m"


def row(label, value, note="", sign=None, sub=False, dim_value=False):
    """One label/value/note line, shared by the terminal and HTML reports."""
    return {"label": label, "chunks": [(value, sign)], "note": note,
            "sub": sub, "dim": dim_value}


def chunks_row(label, chunks, note="", sub=False):
    return {"label": label, "chunks": chunks, "note": note,
            "sub": sub, "dim": False}


def kv_row(r):
    prefix = "    " if r["sub"] else "  "
    label = r["label"]
    if r["dim"]:
        value = dim("".join(t for t, _ in r["chunks"]))
    else:
        value = "".join(signed_text(t, s) for t, s in r["chunks"])
    print(f"{prefix}{dim(f'{label:<32}')}{value:>18}   {dim(r['note'])}")


def build_report(kw):
    """Structured report content, rendered by print_report / write_html."""
    t = kw["trades"]
    overview = [
        row("period", f"{kw['d0']:%d %b %Y} \u2192 {kw['d1']:%d %b %Y}",
            f"{kw['years']:.2f} years"),
        row("initial balance", f"{kw['initial']:,.0f}", "lib/backtest.ml"),
        row("final equity (mark-to-market)", f"{kw['final']:,.0f}",
            f"of which realized {kw['final_realized']:,.0f}"),
        row("net P/L", f"{kw['pnl']:+,.0f}",
            f"open lots marked at {kw['final_unrealized']:+,.0f}",
            polarity(kw["pnl"])),
    ]

    returns = [
        row("total return", f"{kw['total']:+.2%}", "cumulative",
            polarity(kw["total"])),
        row("CAGR", f"{kw['cagr']:+.2%}", "compound annual growth rate",
            polarity(kw["cagr"])),
        row("annualized return", f"{kw['ann_ret']:+.2%}",
            "arithmetic mean \u00d7 %d sessions" % kw["ppy"],
            polarity(kw["ann_ret"])),
        row("annualized volatility", f"{kw['ann_vol']:.2%}",
            "stdev of daily returns \u00d7 \u221a%d" % kw["ppy"]),
        row("return / volatility", f"{kw['ann_ret'] / kw['ann_vol']:.2f}"
            if kw["ann_vol"] else "n/a", "annualized arithmetic ratio"),
        row("best session", f"{kw['best_day_r']:+.2%}",
            f"{kw['best_day']:%d %b %Y}", polarity(kw["best_day_r"])),
        row("worst session", f"{kw['worst_day_r']:+.2%}",
            f"{kw['worst_day']:%d %b %Y}", polarity(kw["worst_day_r"])),
        row("positive sessions", f"{kw['pos_days'] / kw['n_days']:.1%}",
            f"{kw['pos_days']:,} of {kw['n_days']:,}"),
        row("positive months", f"{kw['pos_months'] / len(kw['monthly']):.1%}",
            f"{kw['pos_months']} of {len(kw['monthly'])}"),
    ]

    risk = [
        row("Sharpe ratio", f"{kw['sharpe']:.2f}",
            f"Sharpe 1966, rf {kw['rf']:.2%}"),
        row("Sortino ratio", f"{kw['sortino']:.2f}",
            f"Sortino 1991, MAR {kw['mar']:.1%}"),
        row("Calmar ratio", f"{kw['calmar']:.2f}", "Young 1991: CAGR / |max DD|"),
        row("Omega ratio", f"{kw['omega']:.2f}",
            f"Sortino & Price 1994, {kw['mar']:.1%}"),
        row("Ulcer index", f"{kw['ulcer']:.2f} %", "Martin 1987: RMS of DD%"),
        row("Ulcer performance index", f"{kw['upi']:.2f}",
            "Martin ratio: return / ulcer"),
        row("recovery factor", f"{kw['recovery']:.2f}", "total return / max DD"),
        row("profit factor", f"{kw['profit_factor']:.2f}",
            "gross profit / gross loss"),
    ]

    drawdown = [
        row("maximum drawdown", f"{kw['maxdd']:,.0f}", "in account currency",
            polarity(kw["maxdd"])),
        row("maximum drawdown", f"{kw['maxdd_pct']:.2%}", "of the prior peak",
            polarity(kw["maxdd_pct"])),
        row("peak \u2192 trough",
            f"{kw['maxdd_peak']:%d %b %Y} \u2192 {kw['maxdd_trough']:%d %b %Y}",
            f"{kw['maxdd_len_days']} days"),
        row("recovered", kw["maxdd_rec"], ""),
        row("current drawdown", f"{kw['cur_dd']:.2%}",
            f"{kw['cur_days']} days underwater", polarity(kw["cur_dd"])),
        row("average drawdown", f"{kw['avg_dd']:,.0f}",
            f"{kw['avg_dd_pct']:.2f}% of peak, {kw['n_ep']} episodes",
            polarity(kw["avg_dd"])),
        row("longest drawdown", f"{kw['longest_days']} days",
            f"{kw['longest_fills']} bars below a high"),
        row("time underwater", f"{kw['underwater_pct']:.1%}",
            f"{kw['underwater_days']:,} of {kw['span_days']:,} days"),
    ]
    for n, d in enumerate(kw["top5"], 1):
        pct = d / kw["peak_at_episode"][n - 1] * 100
        drawdown.append(row(f"#{n} deepest", f"{d:,.0f}",
                            f"{pct:.2f}% of that episode's peak",
                            polarity(d), sub=True))

    costs = [
        row("cost model", kw["cost_model"],
            "STRATEGY_COSTS; slippage sits in fill prices"),
        row("total fees", f"{kw['total_fees']:,.0f}",
            f"{kw['total_fees'] / t['n']:,.0f} per closed trade" if t["n"] else ""),
        row("fees / gross profit",
            f"{kw['total_fees'] / kw['gross_win']:.1%}" if kw["gross_win"] else "n/a",
            "share of gross winnings paid away"),
    ]

    trades = [
        row("closed trades", f"{t['n']:,}", f"{t['n'] / kw['years']:.0f} per year"),
        row("win rate", f"{t['win_rate']:.1%}",
            f"{t['wins']:,}W / {t['losses']:,}L"),
        row("average win", f"{t['avg_win']:+,.0f}", "", polarity(t["avg_win"])),
        row("average loss", f"{t['avg_loss']:+,.0f}", "", polarity(t["avg_loss"])),
        row("payoff ratio", f"{t['payoff']:.2f}", "average win / average loss"),
        row("expectancy per trade", f"{t['expectancy']:+,.0f}",
            f"{t['expectancy'] / abs(t['avg_loss']):+.2f}R per trade"
            if t["avg_loss"] else "", polarity(t["expectancy"])),
        row("largest win", f"{t['max_win']:+,.0f}",
            f"{kw['max_win_date']:%d %b %Y}", polarity(t["max_win"])),
        row("largest loss", f"{t['max_loss']:+,.0f}",
            f"{kw['max_loss_date']:%d %b %Y}", polarity(t["max_loss"])),
        row("longest win streak", f"{t['max_win_streak']}", "consecutive"),
        row("longest loss streak", f"{t['max_loss_streak']}", "consecutive"),
        row("average holding period", f"{t['avg_hold_days']:.1f} days",
            f"{t['avg_hold_bars']:.0f} bars"),
        row("time in market", f"{kw['exposure']:.1%}", "bars holding a position"),
    ]

    distribution = [
        row("VaR 95 (1 session)", f"{kw['var95']:.2%}",
            "historical, 5% left tail", polarity(kw["var95"])),
        row("VaR 99 (1 session)", f"{kw['var99']:.2%}",
            "historical, 1% left tail", polarity(kw["var99"])),
        row("CVaR 95 / expected shortfall", f"{kw['cvar95']:.2%}",
            "mean beyond VaR 95", polarity(kw["cvar95"])),
        row("CVaR 99 / expected shortfall", f"{kw['cvar99']:.2%}",
            "mean beyond VaR 99", polarity(kw["cvar99"])),
        row("skewness", f"{kw['skew']:.2f}", "negative = crash risk"),
        row("excess kurtosis", f"{kw['kurt'] - 3:.2f}", "positive = fat tails"),
        row("tail ratio", f"{kw['tail']:.2f}", "|p95| / |p05|"),
        chunks_row("best / worst month",
                   [(f"{kw['best_m']:+.2%}", polarity(kw["best_m"])),
                    (" / ", None),
                    (f"{kw['worst_m']:+.2%}", polarity(kw["worst_m"]))],
                   "monthly closes"),
    ]

    significance = [
        row("probabilistic Sharpe ratio", f"{kw['psr']:.1%}",
            "Bailey & LdP 2012: P(true SR > 0)"),
        row("track record length", f"{kw['n_days']:,} sessions",
            f"{kw['years']:.2f} years"),
        row("MinTRL for SR = 1.0",
            "already met" if kw["mintrl"] == 0 else f"{kw['mintrl'] / kw['ppy']:.2f} years",
            f"95% confidence; observed SR {kw['sharpe']:.2f}"),
    ]
    if kw["trials"]:
        significance += [
            row("E[max SR] over N trials", f"{kw['emax']:.2f}",
                f"Bailey & LdP 2014, N = {kw['trials']:,}"),
            row("deflated Sharpe ratio", f"{kw['dsr']:.1%}",
                f"P(true SR > SR_0 = {kw['emax']:.2f})"),
        ]
    else:
        significance.append(row(
            "E[max SR] over N trials", "--",
            "pass --trials N to deflate for multiple testing", dim_value=True))

    bench = None
    if kw["bench"]:
        b = kw["bench"]
        bench = [
            row("buy & hold total return", f"{b['total']:+.2%}",
                f"{b['final']:,.0f} on the same capital", polarity(b["total"])),
            row("buy & hold CAGR", f"{b['cagr']:+.2%}", "no roll, no carry",
                polarity(b["cagr"])),
            row("buy & hold max drawdown", f"{b['maxdd']:.2%}", "",
                polarity(b["maxdd"])),
            row("buy & hold Sharpe", f"{b['sharpe']:.2f}", "same sessions"),
            row("information ratio", f"{b['ir']:.2f}",
                "active return / tracking error"),
            row("tracking error", f"{b['te']:.2%}", "annualized active volatility"),
            row("beta", f"{b['beta']:.2f}", "vs. the index"),
            row("correlation", f"{b['corr']:.2f}", "daily returns"),
            row("Jensen alpha", f"{b['alpha']:+.2%}", "annualized, rf-adjusted",
                polarity(b["alpha"])),
            row("up / down capture", f"{b['up_cap']:.0%} / {b['down_cap']:.0%}",
                "strategy return in up/down index sessions"),
        ]

    footer = (
        f"equity is mark-to-market: open lots priced at each bar's close. "
        f"Sharpe/Sortino/Omega annualize by \u221a{kw['ppy']:.0f} sessions. "
        f"Formulas: Sharpe 1966 \u00b7 Sortino 1991 \u00b7 Young 1991 (Calmar) \u00b7 "
        f"Sortino & Price 1994 (Omega) \u00b7 Martin 1987 (Ulcer) \u00b7 "
        f"Bailey & L\u00f3pez de Prado 2012/2014 (PSR, MinTRL, DSR)."
    )

    return {
        "title": f"PERFORMANCE REPORT \u00b7 {kw['strategy']} \u00b7 {kw['data']}",
        "subtitle": (f"{kw['n_bars']:,} bars \u00b7 {kw['n_days']:,} sessions "
                     f"\u00b7 {kw['n_trades']:,} trades"),
        "overview": overview,
        "sections": [
            ("RETURNS", returns),
            ("RISK-ADJUSTED", risk),
            ("DRAWDOWN", drawdown),
            ("COSTS", costs),
            ("TRADE STATISTICS", trades),
            ("RETURN DISTRIBUTION", distribution),
            ("STATISTICAL SIGNIFICANCE", significance),
        ],
        "bench": bench,
        "monthly": kw["monthly"],
        "annual": kw["annual"],
        "footer": footer,
    }


def print_report(c):
    print()
    banner(c["title"], c["subtitle"])
    print()
    for r in c["overview"]:
        kv_row(r)
    for title, rows in c["sections"]:
        rule(title)
        for r in rows:
            kv_row(r)

    print()
    rule("MONTHLY RETURNS (%)")
    grid, annual = c["monthly"], c["annual"]
    head = "  " + f"{'year':<6}" + "".join(f"{m:>7}" for m in MONTHS)
    print(dim(head + f"{'year':>9}"))
    for y in sorted(annual):
        cells = [
            f"{'':>7}" if (y, m) not in grid
            else tone(grid[(y, m)] * 100, "{:+7.2f}")
            for m in range(1, 13)
        ]
        print(f"  {dim(f'{y:<6}')}" + "".join(cells) + tone(annual[y] * 100, "{:+9.2f}"))

    if c["bench"]:
        rule("VERSUS BUY & HOLD")
        for r in c["bench"]:
            kv_row(r)

    print()
    print(dim(c["footer"]))
    print()


# -------------------------------------------------------------------- HTML

HTML_CSS = """
:root {
  --bg: #f4f6fb; --panel: #ffffff; --ink: #1b2233; --muted: #6b7385;
  --line: #e3e7f0; --accent: #2f6bf6; --up: #0a8f6c; --down: #cf3d3d;
  --chip: #eef3ff; --shadow: 0 1px 2px rgba(20, 30, 60, .06),
  0 12px 32px rgba(20, 30, 60, .07);
}
[data-theme="dark"] {
  --bg: #0e121c; --panel: #161c29; --ink: #e6eaf3; --muted: #98a1b5;
  --line: #283041; --accent: #7aa2ff; --up: #34c49a; --down: #ff6b6b;
  --chip: #1d2740; --shadow: 0 1px 2px rgba(0, 0, 0, .35),
  0 12px 32px rgba(0, 0, 0, .35);
}
* { box-sizing: border-box; }
html { color-scheme: light; }
[data-theme="dark"] { color-scheme: dark; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.55 Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
  Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 34px 26px 54px; }
.hero { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 22px; }
.hero .eyebrow {
  color: var(--accent); font-size: 11px; font-weight: 800;
  letter-spacing: .16em; text-transform: uppercase; margin-bottom: 6px;
}
.hero h1 { margin: 0; font-size: 29px; line-height: 1.15; letter-spacing: -.02em; }
.hero h1 span { color: var(--muted); font-weight: 500; }
.hero .sub { margin: 7px 0 0; color: var(--muted); font-size: 13px; }
.hero .meta { margin-left: auto; text-align: right; color: var(--muted); font-size: 12px; }
.hero .meta strong { display: block; color: var(--ink); font-size: 13px; }
#theme {
  margin-top: 8px; font: inherit; font-size: 11.5px; padding: 5px 11px;
  border-radius: 999px; border: 1px solid var(--line); background: var(--panel);
  color: var(--ink); cursor: pointer;
}
#theme:hover { border-color: var(--accent); color: var(--accent); }
.cards {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px; margin-bottom: 20px;
}
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  padding: 14px 16px; box-shadow: var(--shadow);
}
.card .k {
  color: var(--muted); font-size: 10.5px; font-weight: 700;
  letter-spacing: .09em; text-transform: uppercase;
}
.card .v {
  margin-top: 6px; font-size: 23px; font-weight: 700;
  letter-spacing: -.02em; font-variant-numeric: tabular-nums;
}
.card .d { margin-top: 3px; color: var(--muted); font-size: 11.5px; }
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
  padding: 20px 22px; margin-bottom: 16px; box-shadow: var(--shadow);
  break-inside: avoid;
}
.panel > h2 {
  margin: 0 0 12px; font-size: 12px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--accent);
}
.panel .caption { color: var(--muted); font-size: 12px; margin: -6px 0 14px; }
.imgwrap { padding: 10px; background: #fff; border-radius: 10px; }
.imgwrap img { width: 100%; height: auto; display: block; }
table.stats { width: 100%; border-collapse: collapse; }
table.stats tr + tr td { border-top: 1px solid var(--line); }
table.stats td { padding: 7px 0; vertical-align: baseline; }
table.stats td.lbl { width: 36%; color: var(--muted); }
table.stats td.val {
  width: 20%; text-align: right; font-weight: 650;
  font-variant-numeric: tabular-nums; white-space: nowrap; padding-right: 22px;
}
table.stats td.note { width: 44%; color: var(--muted); font-size: 12.5px; }
table.stats tr.sub td { padding-top: 2px; padding-bottom: 2px; font-size: 12.5px; }
table.stats tr.sub td.lbl { padding-left: 18px; }
.pos { color: var(--up); }
.neg { color: var(--down); }
.muted { color: var(--muted); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
table.monthly { width: 100%; border-collapse: separate; border-spacing: 3px; }
table.monthly th {
  color: var(--muted); font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .05em; padding: 2px 0 6px;
}
table.monthly td {
  text-align: center; padding: 5px 2px; border-radius: 5px; font-size: 12px;
  font-variant-numeric: tabular-nums; background: var(--chip);
}
table.monthly td.year { background: transparent; color: var(--muted); font-weight: 650; }
table.monthly td.miss { background: transparent; color: var(--muted); }
table.monthly td.annual { background: transparent; font-weight: 700; }
footer {
  margin-top: 24px; color: var(--muted); font-size: 12px;
  border-top: 1px solid var(--line); padding-top: 14px;
}
footer .stamp { margin-top: 6px; font-size: 11px; }
@media (max-width: 900px) {
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .grid2 { grid-template-columns: 1fr; }
  .hero { flex-direction: column; }
  .hero .meta { margin-left: 0; text-align: left; }
  .wrap { padding: 22px 14px 34px; }
}
@media (max-width: 520px) {
  .cards { grid-template-columns: 1fr; }
}
@media print {
  .no-print { display: none !important; }
  body { background: #fff; }
  .panel, .card { box-shadow: none; break-inside: avoid; }
  .wrap { max-width: none; padding: 0; }
}
"""

HTML_JS = """
(function () {
  var root = document.documentElement;
  var btn = document.getElementById("theme");
  function apply(name) {
    root.dataset.theme = name;
    btn.textContent = name === "dark" ? "Light theme" : "Dark theme";
    try { localStorage.setItem("drawdown-theme", name); } catch (e) {}
  }
  btn.addEventListener("click", function () {
    apply(root.dataset.theme === "dark" ? "light" : "dark");
  });
  var saved = null;
  try { saved = localStorage.getItem("drawdown-theme"); } catch (e) {}
  apply(saved || (window.matchMedia &&
    matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
})();
"""

HTML_DOC = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div>
    <div class="eyebrow">Performance report</div>
    <h1>__HEADLINE__</h1>
    <p class="sub">__SUBTITLE__</p>
  </div>
  <div class="meta">
    <strong>__PERIOD__</strong>
    <button id="theme" class="no-print" type="button">Dark theme</button>
  </div>
</header>
__CARDS__
<section class="panel">
  <h2>Equity, drawdown and rolling Sharpe</h2>
  <p class="caption">__CAPTION__</p>
  <div class="imgwrap"><img alt="Equity, drawdown and rolling Sharpe chart"
    src="data:image/png;base64,__PNG__"></div>
</section>
<section class="panel">
  <h2>Account</h2>
  <table class="stats"><tbody>__OVERVIEW__</tbody></table>
</section>
__SECTIONS__
__MONTHLY__
__BENCH__
<footer>
  __FOOTER__
  <div class="stamp">Generated by scripts/drawdown.py on __STAMP__.</div>
</footer>
</div>
<script>__JS__</script>
</body>
</html>
"""


def _esc(x):
    return html.escape(str(x))


def _html_chunks(r):
    parts = []
    for text, sign in r["chunks"]:
        body = _esc(text)
        if sign and sign > 0:
            parts.append(f'<span class="pos">{body}</span>')
        elif sign and sign < 0:
            parts.append(f'<span class="neg">{body}</span>')
        else:
            parts.append(body)
    body = "".join(parts)
    return f'<span class="muted">{body}</span>' if r["dim"] else body


def _html_rows(rows):
    out = []
    for r in rows:
        cls = ' class="sub"' if r["sub"] else ""
        out.append(
            f'<tr{cls}><td class="lbl">{_esc(r["label"])}</td>'
            f'<td class="val">{_html_chunks(r)}</td>'
            f'<td class="note">{_esc(r["note"])}</td></tr>'
        )
    return "\n".join(out)


def _html_cards(kw):
    t = kw["trades"]
    cards = [
        ("Final equity", f"{kw['final']:,.0f}", None,
         f"started at {kw['initial']:,.0f}"),
        ("Net P/L", f"{kw['pnl']:+,.0f}", polarity(kw["pnl"]),
         f"{kw['total']:+.2%} total return"),
        ("CAGR", f"{kw['cagr']:+.2%}", polarity(kw["cagr"]),
         f"over {kw['years']:.2f} years"),
        ("Sharpe ratio", f"{kw['sharpe']:.2f}", None, f"rf {kw['rf']:.2%}"),
        ("Max drawdown", f"{kw['maxdd_pct']:.2%}", polarity(kw["maxdd_pct"]),
         f"{kw['maxdd']:,.0f} on {kw['maxdd_trough']:%d %b %Y}"),
        ("Win rate", f"{t['win_rate']:.1%}", None,
         f"{t['wins']:,}W / {t['losses']:,}L"),
        ("Profit factor", f"{kw['profit_factor']:.2f}", None,
         "gross profit / gross loss"),
        ("Time underwater", f"{kw['underwater_pct']:.1%}", None,
         f"{kw['underwater_days']:,} of {kw['span_days']:,} days"),
    ]
    out = []
    for label, value, sign, note in cards:
        cls = "pos" if sign and sign > 0 else "neg" if sign and sign < 0 else ""
        out.append(
            f'<div class="card"><div class="k">{_esc(label)}</div>'
            f'<div class="v {cls}">{_esc(value)}</div>'
            f'<div class="d">{_esc(note)}</div></div>'
        )
    return '<div class="cards">\n' + "\n".join(out) + "\n</div>"


def _html_monthly(c):
    grid, annual = c["monthly"], c["annual"]
    present = [abs(v) for v in grid.values()] or [1e-9]
    scale = max(present) or 1e-9
    out = [
        '<section class="panel"><h2>Monthly returns</h2>',
        '<p class="caption">Monthly closes, percent. Green/red shading '
        'scales with the size of the move.</p>',
        '<table class="monthly"><thead><tr><th></th>',
    ]
    out += [f"<th>{m}</th>" for m in MONTHS]
    out.append("<th>Year</th></tr></thead><tbody>")
    for y in sorted(annual):
        out.append(f'<tr><td class="year">{y}</td>')
        for m in range(1, 13):
            v = grid.get((y, m))
            if v is None:
                out.append('<td class="miss">\u00b7</td>')
                continue
            alpha = 0.10 + 0.45 * min(abs(v) / scale, 1.0)
            rgb = "38, 166, 154" if v >= 0 else "239, 83, 80"
            out.append(
                f'<td style="background:rgba({rgb},{alpha:.2f})">'
                f'{v * 100:+.2f}</td>')
        a = annual[y]
        cls = "pos" if a > 0 else "neg" if a < 0 else ""
        out.append(f'<td class="annual {cls}">{a * 100:+.2f}</td></tr>')
    out.append("</tbody></table></section>")
    return "\n".join(out)


def write_html_report(path, kw, content, png_bytes):
    """Write the self-contained, styled HTML report."""
    cards = _html_cards(kw)
    sections = []
    for title, rows in content["sections"]:
        sections.append(
            f'<section class="panel"><h2>{_esc(title)}</h2>'
            f'<table class="stats"><tbody>{_html_rows(rows)}</tbody></table>'
            f'</section>'
        )
    bench = ""
    if content["bench"]:
        bench = (
            '<section class="panel"><h2>Versus buy &amp; hold</h2>'
            f'<table class="stats"><tbody>{_html_rows(content["bench"])}'
            '</tbody></table></section>'
        )

    doc = HTML_DOC
    doc = doc.replace("__CSS__", HTML_CSS)
    doc = doc.replace("__JS__", HTML_JS)
    doc = doc.replace("__TITLE__", _esc(content["title"]))
    doc = doc.replace("__HEADLINE__",
                      f"{_esc(kw['strategy'])} <span>on {_esc(kw['data'])}</span>")
    doc = doc.replace("__SUBTITLE__", _esc(content["subtitle"]))
    doc = doc.replace("__PERIOD__",
                      f"{kw['d0']:%d %b %Y} \u2192 {kw['d1']:%d %b %Y}")
    doc = doc.replace(
        "__CAPTION__",
        _esc("Account equity vs. buy & hold, the underwater curve from the "
             "high-water mark, and a rolling 126-session Sharpe ratio."))
    doc = doc.replace("__PNG__", base64.b64encode(png_bytes).decode("ascii"))
    doc = doc.replace("__CARDS__", cards)
    doc = doc.replace("__OVERVIEW__", _html_rows(content["overview"]))
    doc = doc.replace("__SECTIONS__", "\n".join(sections))
    doc = doc.replace("__MONTHLY__", _html_monthly(content))
    doc = doc.replace("__BENCH__", bench)
    doc = doc.replace("__FOOTER__", _esc(content["footer"]))
    doc = doc.replace("__STAMP__", f"{datetime.now():%d %b %Y %H:%M}")
    path.write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "strategy",
        nargs="?",
        choices=list(DUNE_TARGETS),
        default="main",
        help="which bin/ strategy to run (default: main)",
    )
    ap.add_argument("--out", type=Path, default=OUT_PNG)
    ap.add_argument(
        "--initial",
        type=float,
        default=None,
        help="account balance for %% drawdowns (default: lib/backtest.ml initial_balance)",
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
        const=ROOT / "drawdown_report.html",
        default=None,
        metavar="PATH",
        help="write a styled, self-contained HTML report instead of printing "
             "the statistics (default: drawdown_report.html)",
    )
    args = ap.parse_args()

    initial = (
        args.initial
        if args.initial is not None
        else read_ocaml_literal("initial_balance", 3_000_000.0)
    )
    lot_size = read_ocaml_literal("lot_size", 50.0)

    data_name = EMITTER.name.replace("_emitter.py", ".csv")

    out, err = run_backtest(DUNE_TARGETS[args.strategy])
    bars = parse_bars(out)
    if not any(f for _, _, f in bars):
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    equity, net_lots, realized_pnl, trades, total_fees = ledger(bars, lot_size, initial)
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
    dd, peaks, episodes = drawdowns(equity)
    dd_pct = [d / p * 100 for d, p in zip(dd, peaks)]
    worst = min(range(n_bars), key=lambda i: dd[i])
    worst_p = min(range(n_bars), key=lambda i: dd_pct[i])
    worst_ep = episodes[min(range(len(episodes)),
                             key=lambda k: episodes[k]["depth"])]
    depths = [e["depth"] for e in episodes]
    depths_pct = [e["depth"] / peaks[e["trough_i"]] * 100 for e in episodes]
    def ep_days(e):
        """Length of a drawdown episode in sessions, open episodes to today."""
        start = dates[e["peak_i"]].date()
        end = dates[e["recover"]].date() if e["recover"] is not None else days[-1]
        return (end - start).days

    underwater_days = sum(ep_days(e) for e in episodes)
    span_days = (days[-1] - days[0]).days
    rec = worst_ep["recover"]
    # how far the account is below its high-water mark right now
    cur_i = max(range(n_bars), key=lambda i: equity[i])
    cur_days = (days[-1] - dates[cur_i].date()).days

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

    best_t = max(trades, key=lambda t: t["pnl"])
    worst_t = min(trades, key=lambda t: t["pnl"])
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
        "max_win": best_t["pnl"],
        "max_loss": worst_t["pnl"],
        "max_win_streak": streaks(lambda p: p > 0),
        "max_loss_streak": streaks(lambda p: p <= 0),
        "avg_hold_days": sum((t["exit"] - t["entry"]).days for t in trades) / len(trades),
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
    annual = {y: math_prod(rs) - 1 for y, rs in annual.items()}

    bench = None
    bh_equity = [initial * c / day_close[0] for c in day_close]
    bh_bars = [initial * c / closes[0] for c in closes]  # bar-frequency, for the plot
    bh_dd, bh_peaks, _ = drawdowns(bh_equity)
    bh_rets = [bh_equity[i] / bh_equity[i - 1] - 1 for i in range(1, n_days)]
    bh_rets.insert(0, bh_equity[0] / initial - 1)
    if sd_r:
        active = [a - b for a, b in zip(rets, bh_rets)]
        a_mean, a_sd, _, _ = moments(active)
        b_mean, b_sd, _, _ = moments(bh_rets)
        cov = sum((a - mean_r) * (b - b_mean) for a, b in zip(rets, bh_rets)) / (n_days - 1)
        var_b = sum((b - b_mean) ** 2 for b in bh_rets) / (n_days - 1)
        beta = cov / var_b if var_b else 0.0
        corr = cov / (sd_r * b_sd) if b_sd else 0.0
        up = [(a, b) for a, b in zip(rets, bh_rets) if b > 0]
        dn = [(a, b) for a, b in zip(rets, bh_rets) if b < 0]
        bench = {
            "total": bh_equity[-1] / initial - 1,
            "final": bh_equity[-1],
            "cagr": (bh_equity[-1] / initial) ** (1 / years) - 1,
            "maxdd": min(d / p for d, p in zip(bh_dd, bh_peaks)),
            "sharpe": (b_mean - rf_r) / b_sd * sqrt(ppy) if b_sd else float("nan"),
            "ir": a_mean / a_sd * sqrt(ppy) if a_sd else float("nan"),
            "te": a_sd * sqrt(ppy),
            "beta": beta,
            "corr": corr,
            "alpha": (mean_r - rf_r - beta * (b_mean - rf_r)) * ppy,
            "up_cap": (sum(a for a, _ in up) / len(up)) /
                      (sum(b for _, b in up) / len(up)) if up else 0.0,
            "down_cap": (sum(a for a, _ in dn) / len(dn)) /
                        (sum(b for _, b in dn) / len(dn)) if dn else 0.0,
        }

    sr_period = mean_r / sd_r if sd_r else float("nan")
    psr0 = psr(n_days, sr_period, skew, kurt) if sd_r else float("nan")
    # MinTRL answers "how long until the Sharpe is *significantly* above 1.0?"
    # (per-period equivalent, since PSR works in per-period units)
    sr_target = 1.0 / sqrt(ppy)
    mintrl = (
        0.0 if sd_r and sr_period >= sr_target
        else min_track_record(sr_period, sr_target, skew, kurt) if sd_r else float("nan")
    )
    var_sharpe = sharpe_var(n_days, sr_period, skew, kurt) * ppy if sd_r else 0.0
    emax = expected_max_sharpe(args.trials, var_sharpe)
    # PSR wants per-period units, so the deflation benchmark converts too
    dsr = (
        psr(n_days, sr_period, skew, kurt, sr_star=emax / sqrt(ppy))
        if sd_r and emax > 0 else float("nan")
    )

    top5_eps = sorted(episodes, key=lambda e: e["depth"])[:5]

    kw = dict(
        strategy=args.strategy, data=data_name,
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
        maxdd=dd[worst], maxdd_pct=maxdd_pct_dec,
        maxdd_peak=dates[worst_ep["peak_i"]], maxdd_trough=dates[worst],
        maxdd_len_days=(dates[worst] - dates[worst_ep["peak_i"]]).days,
        maxdd_rec=(f"{dates[rec]:%d %b %Y}" if rec is not None else "not yet (open)"),
        cur_dd=dd[-1] / peaks[-1], cur_days=cur_days,
        avg_dd=sum(depths) / len(depths), avg_dd_pct=sum(depths_pct) / len(depths_pct),
        n_ep=len(episodes), longest_days=max(ep_days(e) for e in episodes),
        longest_fills=max((e["recover"] or n_bars - 1) - e["peak_i"] for e in episodes),
        underwater_pct=underwater_days / span_days,
        underwater_days=underwater_days, span_days=span_days,
        top5=[e["depth"] for e in top5_eps],
        peak_at_episode=[peaks[e["trough_i"]] for e in top5_eps],
        trades=tstats, max_win_date=best_t["exit"], max_loss_date=worst_t["exit"],
        total_fees=total_fees, gross_win=gross_win,
        cost_model=cost_model_name(),
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
    )
    content = build_report(kw)
    if args.export is None:
        print_report(content)

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
    ax1.plot([dates[worst]], [equity[worst]], "o", color="tab:red", ms=6, zorder=5)
    ax1.annotate(
        f"max drawdown\n{dd[worst]:,.0f} ({dd_pct[worst]:.1f}% of peak)\n"
        f"{dates[worst]:%d %b %Y}",
        (dates[worst], equity[worst]), textcoords="offset points",
        xytext=(10, -52), ha="left", fontsize=9, color="tab:red", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="tab:red", lw=1.0),
    )
    ax1.set_title(
        f"{args.strategy} on {data_name} \u2014 equity, benchmark and drawdowns",
        fontsize=14, pad=12,
    )
    ax1.set_ylabel("Account equity ($)")
    ax1.yaxis.set_major_formatter(money)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=9)

    ax2.fill_between(dates, 0, dd_pct, color="tab:red", alpha=0.45)
    ax2.plot(dates, dd_pct, color="tab:red", lw=1.1)
    ax2.axhline(sum(depths_pct) / len(depths_pct), color="0.35", lw=1.0, ls="--",
                label=f"average drawdown {sum(depths_pct) / len(depths_pct):.2f}%")
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
    for i in range(window, n_days):
        w = rets[i - window:i]
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
        f"underwater {underwater_days / span_days:.0%}  |  "
        f"script: scripts/drawdown.py",
        ha="right", fontsize=8.5, color="0.35",
    )
    fig.subplots_adjust(hspace=0.14, left=0.085, right=0.97, top=0.955, bottom=0.06)
    fig.savefig(args.out, dpi=150, facecolor="white")
    print(f"plot saved to:  {args.out}")

    if args.export is not None:
        write_html_report(args.export, kw, content, args.out.read_bytes())
        print(f"report saved to: {args.export}")


def math_prod(xs):
    """Compound a list of simple returns."""
    out = 1.0
    for x in xs:
        out *= 1 + x
    return out


if __name__ == "__main__":
    main()
