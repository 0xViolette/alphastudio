"""Performance analysis shared by all tearsheet workflows."""

from dataclasses import dataclass
from math import isfinite, sqrt

from ledger import is_zero, replay
from metrics import (
    cvar,
    downside_deviation,
    drawdowns,
    expected_max_sharpe,
    math_prod,
    min_track_record,
    moments,
    psr,
    quantile,
    sharpe_var,
    ulcer_index,
)


@dataclass(slots=True)
class Performance:
    metrics: dict
    dates: list
    equity: list[float]
    peaks: list[float]
    drawdown: list[float]
    drawdown_pct: list[float]
    benchmark_equity: list[float]
    days: list
    day_equity: list[float]
    returns: list[float]

    @property
    def worst_i(self):
        return min(range(len(self.drawdown_pct)), key=self.drawdown_pct.__getitem__)


def analyze(
    bars,
    *,
    lot_size,
    initial,
    strategy,
    data,
    cost_model,
    rf=0.0,
    mar=0.0,
    trials=0,
    initial_source="cost configuration",
    benchmark_equity=None,
    account=None,
):
    if not bars:
        raise ValueError("at least two sessions are required for an annualized tearsheet")
    if not isfinite(initial) or initial <= 0:
        raise ValueError("initial equity must be positive")
    if benchmark_equity is not None and len(benchmark_equity) != len(bars):
        raise ValueError("benchmark equity must align with strategy bars")

    account = account or replay(bars, lot_size, initial)
    equity = account.equity
    trades = account.trades
    dates = [bar.dt for bar in bars]
    n_bars = len(bars)
    day_index = {}
    for i, dt in enumerate(dates):
        day_index[dt.date()] = i
    days = sorted(day_index)
    day_equity = [equity[day_index[day]] for day in days]
    n_days = len(days)
    if n_days < 2:
        raise ValueError("at least two sessions are required for an annualized tearsheet")
    for dt, value in zip(dates, equity):
        if not isfinite(value) or value <= 0:
            raise ValueError(
                f"account equity became non-positive on {dt:%Y-%m-%d %H:%M}; "
                "return-based statistics are undefined"
            )

    rets = [day_equity[i] / day_equity[i - 1] - 1 for i in range(1, n_days)]
    rets.insert(0, day_equity[0] / initial - 1)
    years = (days[-1] - days[0]).days / 365.25
    ppy = n_days / years
    mean_r, sd_r, skew, kurt = moments(rets)
    ann_ret = mean_r * ppy
    ann_vol = sd_r * sqrt(ppy)
    rf_r, mar_r = rf / ppy, mar / ppy
    total = equity[-1] / initial - 1
    cagr = (equity[-1] / initial) ** (1 / years) - 1

    dd, peaks, episodes = drawdowns(equity, initial)
    dd_pct = [value / peak * 100 for value, peak in zip(dd, peaks)]
    worst_p = min(range(n_bars), key=lambda i: dd_pct[i])
    worst_ep = min(
        episodes,
        key=lambda episode: episode["depth"] / peaks[episode["trough_i"]],
        default=None,
    )
    depths = [episode["depth"] for episode in episodes]
    depths_pct = [
        episode["depth"] / peaks[episode["trough_i"]] * 100
        for episode in episodes
    ]

    def ep_days(episode):
        start = dates[episode["peak_i"]].date()
        end = (
            dates[episode["recover"]].date()
            if episode["recover"] is not None
            else days[-1]
        )
        return (end - start).days

    def ep_bars(episode):
        end = (
            episode["recover"]
            if episode["recover"] is not None
            else n_bars - 1
        )
        return sum(1 for i in range(episode["peak_i"], end + 1) if dd[i] < 0)

    underwater_days = sum(ep_days(episode) for episode in episodes)
    span_days = (days[-1] - days[0]).days
    rec = worst_ep["recover"] if worst_ep is not None else None
    longest_ep = max(episodes, key=ep_days, default=None)
    cur_ep = episodes[-1] if episodes and episodes[-1]["recover"] is None else None
    cur_days = (days[-1] - dates[cur_ep["peak_i"]].date()).days if cur_ep else 0

    downside = downside_deviation(rets, mar_r)
    omega_num = sum(max(r - mar_r, 0.0) for r in rets)
    omega_den = sum(max(mar_r - r, 0.0) for r in rets)
    ulcer = ulcer_index(dd_pct)
    maxdd_pct_dec = dd_pct[worst_p] / 100
    sharpe = (mean_r - rf_r) / sd_r * sqrt(ppy) if sd_r else float("nan")
    sortino = (mean_r - mar_r) / downside * sqrt(ppy) if downside else float("nan")
    calmar = cagr / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan")

    pnls = [trade.pnl for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    def streaks(pred):
        best = current = 0
        for pnl in pnls:
            current = current + 1 if pred(pnl) else 0
            best = max(best, current)
        return best

    best_t = max(
        (trade for trade in trades if trade.pnl > 0),
        key=lambda trade: trade.pnl,
        default=None,
    )
    worst_t = min(
        (trade for trade in trades if trade.pnl <= 0),
        key=lambda trade: trade.pnl,
        default=None,
    )
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
        "max_win": best_t.pnl if best_t else None,
        "max_loss": worst_t.pnl if worst_t else None,
        "max_win_streak": streaks(lambda pnl: pnl > 0),
        "max_loss_streak": streaks(lambda pnl: pnl <= 0),
        "avg_hold_days": sum(
            (trade.exit - trade.entry).total_seconds() / 86400 for trade in trades
        ) / len(trades) if trades else 0.0,
        "avg_hold_bars": sum(trade.bars for trade in trades) / len(trades)
        if trades else 0.0,
    }

    month_end = {}
    for day, value in zip(days, day_equity):
        month_end[(day.year, day.month)] = value
    monthly, annual = {}, {}
    previous = initial
    for key in sorted(month_end):
        month_return = month_end[key] / previous - 1
        monthly[key] = month_return
        annual.setdefault(key[0], []).append(month_return)
        previous = month_end[key]
    annual = {year: math_prod(1 + r for r in returns) - 1 for year, returns in annual.items()}

    monthly_points, annual_points = {}, {}
    for trade in trades:
        key = (trade.exit.year, trade.exit.month)
        monthly_points[key] = monthly_points.get(key, 0.0) + trade.points
    for key, value in monthly_points.items():
        annual_points[key[0]] = annual_points.get(key[0], 0.0) + value

    bench = None
    bh_bars = list(benchmark_equity) if benchmark_equity is not None else []
    if benchmark_equity is not None:
        bh_equity = [bh_bars[day_index[day]] for day in days]
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
            cov = sum(
                (a - mean_r) * (b - b_mean) for a, b in zip(rets, bh_rets)
            ) / (n_days - 1)
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
                "alpha": (mean_r - rf_r - beta * (b_mean - rf_r)) * ppy
                if isfinite(beta) else float("nan"),
                "up_cap": (sum(a for a, _ in up) / len(up)) /
                          (sum(b for _, b in up) / len(up))
                if up and sum(b for _, b in up) else float("nan"),
                "down_cap": (sum(a for a, _ in dn) / len(dn)) /
                            (sum(b for _, b in dn) / len(dn))
                if dn and sum(b for _, b in dn) else float("nan"),
            })

    sr_period = (mean_r - rf_r) / sd_r if sd_r else float("nan")
    psr0 = psr(n_days, sr_period, skew, kurt) if sd_r else float("nan")
    sr_target = 1.0 / sqrt(ppy)
    mintrl = (
        0.0 if sd_r and psr(n_days, sr_period, skew, kurt, sr_target) >= 0.95
        else min_track_record(sr_period, sr_target, skew, kurt)
        if sd_r else float("nan")
    )
    var_sharpe = sharpe_var(n_days, sr_period, skew, kurt) * ppy if sd_r else 0.0
    emax = expected_max_sharpe(trials, var_sharpe)
    dsr = (
        psr(n_days, sr_period, skew, kurt, sr_star=emax / sqrt(ppy))
        if sd_r and trials > 0 else float("nan")
    )
    top5_eps = sorted(
        episodes, key=lambda episode: episode["depth"] / peaks[episode["trough_i"]]
    )[:5]

    kw = dict(
        strategy=strategy, data=data,
        n_bars=n_bars, n_days=n_days, n_trades=len(trades),
        d0=days[0], d1=days[-1], years=years,
        initial=initial, final=equity[-1], pnl=equity[-1] - initial, total=total,
        final_realized=initial + account.realized_pnl[-1],
        final_unrealized=equity[-1] - initial - account.realized_pnl[-1],
        cagr=cagr, ann_ret=ann_ret, ann_vol=ann_vol, ppy=ppy,
        best_day_r=max(rets),
        best_day=days[max(range(n_days), key=lambda i: rets[i])],
        worst_day_r=min(rets),
        worst_day=days[min(range(n_days), key=lambda i: rets[i])],
        pos_days=sum(1 for r in rets if r > 0), monthly=monthly,
        rf=rf, mar=mar, sharpe=sharpe, sortino=sortino, calmar=calmar,
        omega=omega_num / omega_den if omega_den else float("nan"),
        ulcer=ulcer, upi=ann_ret * 100 / ulcer if ulcer else float("nan"),
        recovery=total / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan"),
        profit_factor=gross_win / gross_loss if gross_loss else float("nan"),
        maxdd=dd[worst_p], maxdd_pct=maxdd_pct_dec,
        maxdd_peak=dates[worst_ep["peak_i"]] if worst_ep else dates[0],
        maxdd_trough=dates[worst_p],
        maxdd_len_days=(dates[worst_p] - dates[worst_ep["peak_i"]]).days
        if worst_ep else 0,
        maxdd_rec=f"{dates[rec]:%d %b %Y}" if rec is not None
        else "not yet (open)" if worst_ep else "n/a",
        cur_dd=dd[-1] / peaks[-1], cur_days=cur_days,
        avg_dd=sum(depths) / len(depths) if depths else 0.0,
        avg_dd_pct=sum(depths_pct) / len(depths_pct) if depths_pct else 0.0,
        n_ep=len(episodes),
        longest_days=ep_days(longest_ep) if longest_ep else 0,
        longest_fills=ep_bars(longest_ep) if longest_ep else 0,
        underwater_pct=underwater_days / span_days if span_days else 0.0,
        underwater_days=underwater_days, span_days=span_days,
        top5=[episode["depth"] for episode in top5_eps],
        peak_at_episode=[peaks[episode["trough_i"]] for episode in top5_eps],
        trades=tstats,
        max_win_date=best_t.exit if best_t else None,
        max_loss_date=worst_t.exit if worst_t else None,
        total_fees=account.fees, gross_win=gross_win,
        cost_model=cost_model, initial_source=initial_source,
        exposure=sum(1 for qty in account.net_lots if not is_zero(qty)) / n_bars,
        var95=quantile(rets, 0.05), var99=quantile(rets, 0.01),
        cvar95=cvar(rets, 0.05), cvar99=cvar(rets, 0.01),
        skew=skew, kurt=kurt,
        tail=abs(quantile(rets, 0.95)) / abs(quantile(rets, 0.05))
        if quantile(rets, 0.05) else float("nan"),
        pos_months=sum(1 for r in monthly.values() if r > 0),
        best_m=max(monthly.values()), worst_m=min(monthly.values()),
        psr=psr0, mintrl=mintrl, trials=trials, emax=emax, dsr=dsr,
        annual=annual, bench=bench,
        monthly_points=monthly_points, annual_points=annual_points,
    )
    return Performance(
        metrics=kw,
        dates=dates,
        equity=equity,
        peaks=peaks,
        drawdown=dd,
        drawdown_pct=dd_pct,
        benchmark_equity=bh_bars,
        days=days,
        day_equity=day_equity,
        returns=rets,
    )
