"""View-neutral report content for performance tearsheets."""

from math import isfinite

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def polarity(value):
    try:
        if value > 0:
            return 1
        if value < 0:
            return -1
    except TypeError:
        pass
    return None


def row(label, value, note="", sign=None, sub=False, dim_value=False):
    return {"label": label, "chunks": [(value, sign)], "note": note,
            "sub": sub, "dim": dim_value}


def chunks_row(label, chunks, note="", sub=False):
    return {"label": label, "chunks": chunks, "note": note,
            "sub": sub, "dim": False}


def build_report(kw):
    """Structured report content, rendered by print_report / write_html."""
    t = kw["trades"]
    overview = [
        row("period", f"{kw['d0']:%d %b %Y} \u2192 {kw['d1']:%d %b %Y}",
            f"{kw['years']:.2f} years"),
        row("initial balance", f"{kw['initial']:,.0f}",
            kw.get("initial_source", "cost configuration")),
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
            "cost configuration; slippage sits in fill prices"),
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
        row("largest win",
            f"{t['max_win']:+,.0f}" if t["max_win"] is not None else "n/a",
            f"{kw['max_win_date']:%d %b %Y}" if kw["max_win_date"] else "",
            polarity(t["max_win"])),
        row("largest loss",
            f"{t['max_loss']:+,.0f}" if t["max_loss"] is not None else "n/a",
            f"{kw['max_loss_date']:%d %b %Y}" if kw["max_loss_date"] else "",
            polarity(t["max_loss"])),
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
            "already met" if kw["mintrl"] == 0
            else f"{kw['mintrl'] / kw['ppy']:.2f} years"
            if isfinite(kw["mintrl"]) else "not attainable at observed SR",
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
        f"Sharpe/Sortino annualize by \u221a{kw['ppy']:.0f} sessions; Omega is not annualized. "
        f"Formulas: Sharpe 1966 \u00b7 Sortino 1991 \u00b7 Young 1991 (Calmar) \u00b7 "
        f"Sortino & Price 1994 (Omega) \u00b7 Martin 1987 (Ulcer) \u00b7 "
        f"Bailey & L\u00f3pez de Prado 2012/2014 (PSR, MinTRL, DSR)."
    )

    sections = [
        ("RETURNS", returns),
        ("RISK-ADJUSTED", risk),
        ("DRAWDOWN", drawdown),
        ("COSTS", costs),
        ("TRADE STATISTICS", trades),
        ("RETURN DISTRIBUTION", distribution),
        ("STATISTICAL SIGNIFICANCE", significance),
    ]

    return {
        "title": f"PERFORMANCE REPORT \u00b7 {kw['strategy']} \u00b7 {kw['data']}",
        "subtitle": (f"{kw['n_bars']:,} bars \u00b7 {kw['n_days']:,} sessions "
                     f"\u00b7 {kw['n_trades']:,} trades"),
        "overview": overview,
        "sections": sections,
        "bench": bench,
        "monthly": kw["monthly"],
        "monthly_points": kw.get("monthly_points", {}),
        "annual": kw["annual"],
        "annual_points": kw.get("annual_points", {}),
        "footer": footer,
    }

