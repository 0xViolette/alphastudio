"""Statistical helpers and account-level performance analysis."""

from dataclasses import dataclass
from math import exp, isfinite, sqrt
from statistics import NormalDist

from .ledger import is_zero, replay

NORMAL = NormalDist()


def moments(xs):
    """(mean, sample stdev, skewness, non-excess kurtosis)."""
    n = len(xs)
    if n < 2:
        return (sum(xs) / n if n else 0.0), 0.0, 0.0, 3.0
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
    var = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    return NORMAL.cdf((sr - sr_star) * sqrt(n - 1) / sqrt(var))


def min_track_record(sr, sr_star, g3, g4, alpha=0.05):
    """MinTRL: periods needed for SR to beat SR* with 1 - alpha confidence."""
    if sr <= sr_star:
        return float("inf")
    var = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    return 1 + var * (NORMAL.inv_cdf(1 - alpha) / (sr - sr_star)) ** 2


def expected_max_sharpe(trials, var_sharpe):
    """E[max SR] over `trials` independent trials with the same variance."""
    if trials < 2:
        return 0.0
    euler = 0.5772156649
    g = (1 - euler) * NORMAL.inv_cdf(1 - 1 / trials)
    g += euler * NORMAL.inv_cdf(1 - 1 / (trials * exp(1)))
    return sqrt(var_sharpe) * g


def ulcer_index(dd_pct):
    """Martin (1987): RMS of the percentage drawdown series."""
    return sqrt(sum(d * d for d in dd_pct) / len(dd_pct))


def drawdowns(equity, initial=None):
    """Return (dd_abs, peak_abs, episodes) for a list of equity values."""
    run_peak = float("-inf") if initial is None else initial
    dd, peaks = [], []
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
                cur = {"peak_i": max(i - 1, 0), "trough_i": i, "recover": None}
            if dd[i] < dd[cur["trough_i"]]:
                cur["trough_i"] = i
    if cur is not None:
        episodes.append(cur)
    for ep in episodes:
        ep["depth"] = dd[ep["trough_i"]]
    return dd, peaks, episodes


def math_prod(xs):
    p = 1.0
    for x in xs:
        p *= x
    return p


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
        raise ValueError("at least two sessions are required for an annualized report")
    if not isfinite(initial) or initial <= 0:
        raise ValueError("initial equity must be positive")
    if benchmark_equity is not None and len(benchmark_equity) != len(bars):
        raise ValueError("benchmark equity must align with strategy bars")

    account = account or replay(bars, initial)
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
        raise ValueError("at least two sessions are required for an annualized report")
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
    trade_stats = {
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
    annual = {
        year: math_prod(1 + r for r in returns) - 1
        for year, returns in annual.items()
    }

    monthly_points, annual_points = {}, {}
    for trade in trades:
        key = (trade.exit.year, trade.exit.month)
        monthly_points[key] = monthly_points.get(key, 0.0) + trade.points
    for key, value in monthly_points.items():
        annual_points[key[0]] = annual_points.get(key[0], 0.0) + value

    bench = None
    bench_bars = list(benchmark_equity) if benchmark_equity is not None else []
    if benchmark_equity is not None:
        bh_equity = [bench_bars[day_index[day]] for day in days]
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
            "ir": float("nan"),
            "te": float("nan"),
            "beta": float("nan"),
            "corr": float("nan"),
            "alpha": float("nan"),
            "up_cap": float("nan"),
            "down_cap": float("nan"),
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

    values = dict(
        strategy=strategy,
        data=data,
        n_bars=n_bars,
        n_days=n_days,
        n_trades=len(trades),
        d0=days[0],
        d1=days[-1],
        years=years,
        initial=initial,
        final=equity[-1],
        pnl=equity[-1] - initial,
        total=total,
        final_realized=initial + account.realized_pnl[-1],
        final_unrealized=equity[-1] - initial - account.realized_pnl[-1],
        cagr=cagr,
        ann_ret=ann_ret,
        ann_vol=ann_vol,
        ppy=ppy,
        best_day_r=max(rets),
        best_day=days[max(range(n_days), key=lambda i: rets[i])],
        worst_day_r=min(rets),
        worst_day=days[min(range(n_days), key=lambda i: rets[i])],
        pos_days=sum(1 for r in rets if r > 0),
        monthly=monthly,
        rf=rf,
        mar=mar,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        omega=omega_num / omega_den if omega_den else float("nan"),
        ulcer=ulcer,
        upi=ann_ret * 100 / ulcer if ulcer else float("nan"),
        recovery=total / abs(maxdd_pct_dec) if maxdd_pct_dec else float("nan"),
        profit_factor=gross_win / gross_loss if gross_loss else float("nan"),
        maxdd=dd[worst_p],
        maxdd_pct=maxdd_pct_dec,
        maxdd_peak=dates[worst_ep["peak_i"]] if worst_ep else dates[0],
        maxdd_trough=dates[worst_p],
        maxdd_len_days=(dates[worst_p] - dates[worst_ep["peak_i"]]).days
        if worst_ep else 0,
        maxdd_rec=f"{dates[rec]:%d %b %Y}" if rec is not None
        else "not yet (open)" if worst_ep else "n/a",
        cur_dd=dd[-1] / peaks[-1],
        cur_days=cur_days,
        avg_dd=sum(depths) / len(depths) if depths else 0.0,
        avg_dd_pct=sum(depths_pct) / len(depths_pct) if depths else 0.0,
        n_ep=len(episodes),
        longest_days=ep_days(longest_ep) if longest_ep else 0,
        longest_fills=ep_bars(longest_ep) if longest_ep else 0,
        underwater_pct=underwater_days / span_days if span_days else 0.0,
        underwater_days=underwater_days,
        span_days=span_days,
        top5=[episode["depth"] for episode in top5_eps],
        peak_at_episode=[peaks[episode["trough_i"]] for episode in top5_eps],
        trades=trade_stats,
        max_win_date=best_t.exit if best_t else None,
        max_loss_date=worst_t.exit if worst_t else None,
        total_fees=account.fees,
        gross_win=gross_win,
        cost_model=cost_model,
        initial_source=initial_source,
        exposure=sum(1 for qty in account.net_qty if not is_zero(qty)) / n_bars,
        var95=quantile(rets, 0.05),
        var99=quantile(rets, 0.01),
        cvar95=cvar(rets, 0.05),
        cvar99=cvar(rets, 0.01),
        skew=skew,
        kurt=kurt,
        tail=abs(quantile(rets, 0.95)) / abs(quantile(rets, 0.05))
        if quantile(rets, 0.05) else float("nan"),
        pos_months=sum(1 for r in monthly.values() if r > 0),
        best_m=max(monthly.values()),
        worst_m=min(monthly.values()),
        psr=psr0,
        mintrl=mintrl,
        trials=trials,
        emax=emax,
        dsr=dsr,
        annual=annual,
        bench=bench,
        monthly_points=monthly_points,
        annual_points=annual_points,
    )
    return Performance(
        metrics=values,
        dates=dates,
        equity=equity,
        peaks=peaks,
        drawdown=dd,
        drawdown_pct=dd_pct,
        benchmark_equity=bench_bars,
        days=days,
        day_equity=day_equity,
        returns=rets,
    )


def benchmark_daily_returns(performance):
    """Align benchmark returns with the strategy's session-ending values."""
    if not performance.benchmark_equity:
        return []
    day_index = {}
    for index, dt in enumerate(performance.dates):
        day_index[dt.date()] = index
    equity = [
        performance.benchmark_equity[day_index[day]] for day in performance.days
    ]
    initial = performance.metrics["initial"]
    returns = [equity[index] / equity[index - 1] - 1 for index in range(1, len(equity))]
    returns.insert(0, equity[0] / initial - 1)
    return returns


def rolling_analysis(performance, window=126):
    """Session-aligned rolling Sharpe, Sortino, volatility, and beta."""
    count = len(performance.days)
    result = {"sharpe": [None] * count, "sortino": [None] * count,
              "volatility": [None] * count, "beta": [None] * count}
    benchmark = benchmark_daily_returns(performance)
    ppy = performance.metrics["ppy"]
    rf = performance.metrics["rf"] / ppy
    mar = performance.metrics["mar"] / ppy
    for end in range(window - 1, count):
        start = end - window + 1
        sample = performance.returns[start:end + 1]
        mean, sd, _, _ = moments(sample)
        downside = downside_deviation(sample, mar)
        result["sharpe"][end] = (mean - rf) / sd * sqrt(ppy) if sd else None
        result["sortino"][end] = (mean - mar) / downside * sqrt(ppy) if downside else None
        result["volatility"][end] = sd * sqrt(ppy)
        if benchmark:
            bench = benchmark[start:end + 1]
            bench_mean = sum(bench) / window
            bench_var = sum((value - bench_mean) ** 2 for value in bench) / (window - 1)
            covariance = sum(
                (a - mean) * (b - bench_mean) for a, b in zip(sample, bench)
            ) / (window - 1)
            result["beta"][end] = covariance / bench_var if bench_var else None
    return result


def worst_drawdowns(performance, limit=10):
    """Return the deepest drawdown episodes, deepest first."""
    dd, peaks, episodes = drawdowns(
        performance.equity, performance.metrics["initial"]
    )
    ordered = sorted(
        episodes,
        key=lambda episode: episode["depth"] / peaks[episode["trough_i"]],
    )[:limit]
    rows = []
    for episode in ordered:
        peak_i = episode["peak_i"]
        recover = episode["recover"]
        end_i = recover if recover is not None else len(performance.dates) - 1
        rows.append({
            "start": performance.dates[peak_i].strftime("%Y-%m-%d"),
            "trough": performance.dates[episode["trough_i"]].strftime("%Y-%m-%d"),
            "recovered": performance.dates[recover].strftime("%Y-%m-%d")
            if recover is not None else "Open",
            "depth": episode["depth"] / peaks[episode["trough_i"]],
            "days": (performance.dates[end_i].date() - performance.dates[peak_i].date()).days,
        })
    return rows


def eoy_comparison(performance):
    """Compounded yearly strategy returns with an optional benchmark comparison."""
    strategy = performance.metrics["annual"]
    benchmark = benchmark_daily_returns(performance)
    benchmark_annual = {}
    if benchmark:
        for day, value in zip(performance.days, benchmark):
            benchmark_annual.setdefault(day.year, []).append(value)
        benchmark_annual = {
            year: math_prod(1 + value for value in values) - 1
            for year, values in benchmark_annual.items()
        }
    rows = []
    for year, strategy_return in sorted(strategy.items()):
        benchmark_return = benchmark_annual.get(year)
        rows.append({
            "year": year,
            "strategy": strategy_return,
            "benchmark": benchmark_return,
            "multiplier": (1 + strategy_return) / (1 + benchmark_return)
            if benchmark_return is not None and benchmark_return != -1 else None,
            "won": strategy_return > benchmark_return
            if benchmark_return is not None else None,
        })
    return rows


def return_histogram(returns, bins=30):
    """Histogram counts with a same-scale normal-density overlay."""
    if not returns:
        return []
    lower, upper = min(returns), max(returns)
    if lower == upper:
        return [{"x": lower, "count": len(returns), "density": len(returns)}]
    width = (upper - lower) / bins
    counts = [0] * bins
    for value in returns:
        index = min(int((value - lower) / width), bins - 1)
        counts[index] += 1
    mean, sd, _, _ = moments(returns)
    normal = NormalDist(mean, sd) if sd else None
    output = []
    for index, count in enumerate(counts):
        center = lower + (index + 0.5) * width
        density = normal.pdf(center) * len(returns) * width if normal else 0.0
        output.append({"x": center, "count": count, "density": density})
    return output


def distribution_analysis(performance):
    """Daily distribution plus the monthly-return histogram."""
    return {
        "daily": return_histogram(performance.returns),
        "monthly": return_histogram(list(performance.metrics["monthly"].values())),
    }
