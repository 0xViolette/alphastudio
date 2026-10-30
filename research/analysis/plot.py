"""Matplotlib rendering for performance tearsheets."""

from math import sqrt

from metrics import moments


def write_performance_plot(
    result,
    path,
    *,
    title,
    equity_label,
    benchmark_label,
    script_label,
):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    kw = result.metrics
    dates = result.dates
    equity = result.equity
    peaks = result.peaks
    dd = result.drawdown
    dd_pct = result.drawdown_pct
    initial = kw["initial"]
    worst_p = result.worst_i
    n_bars = len(dates)

    money = FuncFormatter(lambda value, _: f"{value:,.0f}")
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(13, 11), sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1, 1], "hspace": 0.14},
    )

    ax1.plot(dates, equity, color="tab:blue", lw=1.3, label=equity_label)
    ax1.plot(
        dates, result.benchmark_equity, color="0.45", lw=1.0, ls="-.",
        label=benchmark_label,
    )
    ax1.plot(dates, peaks, color="0.55", lw=1.0, ls="--", label="Equity high-water mark")
    ax1.fill_between(
        dates, equity, peaks, color="tab:red", alpha=0.30,
        label="Drawdown (underwater)",
    )
    ax1.fill_between(
        dates, equity, initial, where=[value >= initial for value in equity],
        color="tab:green", alpha=0.10, label="Above starting balance",
    )
    ax1.axhline(initial, color="0.4", lw=0.9, ls=":")
    ax1.annotate(
        f"start {initial:,.0f}", (dates[0], initial),
        textcoords="offset points", xytext=(4, -14), fontsize=8, color="0.35",
    )
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
    ax1.set_title(title, fontsize=14, pad=12)
    ax1.set_ylabel("Account equity")
    ax1.yaxis.set_major_formatter(money)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=9)

    ax2.fill_between(dates, 0, dd_pct, color="tab:red", alpha=0.45)
    ax2.plot(dates, dd_pct, color="tab:red", lw=1.1)
    ax2.axhline(
        kw["avg_dd_pct"], color="0.35", lw=1.0, ls="--",
        label=f"average drawdown {kw['avg_dd_pct']:.2f}%",
    )
    ax2.plot([dates[worst_p]], [dd_pct[worst_p]], "o", color="darkred", ms=6, zorder=5)
    ax2.annotate(
        f"max {dd_pct[worst_p]:.2f}%  ({dd[worst_p]:,.0f})\n"
        f"{dates[worst_p]:%d %b %Y}",
        (dates[worst_p], dd_pct[worst_p]), textcoords="offset points",
        xytext=(14, -6), fontsize=9, color="darkred", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="darkred", lw=1.0),
    )
    ax2.legend(loc="lower left", framealpha=0.9, fontsize=9)
    ax2.set_title(
        "Underwater curve — drawdown from the equity high-water mark",
        fontsize=12, pad=8,
    )
    ax2.set_ylabel("Drawdown from peak (%)")
    ax2.set_ylim(min(dd_pct) * 1.25, 1.0)
    ax2.grid(True, alpha=0.3)

    window = 126
    rolling = []
    rf_r = kw["rf"] / kw["ppy"]
    for i in range(window - 1, len(result.days)):
        sample = result.returns[i - window + 1:i + 1]
        mean, sd, _, _ = moments(sample)
        rolling.append((
            result.days[i],
            (mean - rf_r) / sd * sqrt(kw["ppy"]) if sd else float("nan"),
        ))
    ax3.plot(
        [day for day, _ in rolling], [value for _, value in rolling],
        color="tab:purple", lw=1.1, label=f"rolling {window}-session Sharpe",
    )
    ax3.axhline(
        kw["sharpe"], color="0.35", lw=1.0, ls="--",
        label=f"full-period Sharpe {kw['sharpe']:.2f}",
    )
    ax3.axhline(0.0, color="0.7", lw=0.8, ls=":")
    ax3.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax3.set_title(
        "Rolling Sharpe ratio — is the edge persistent or a spike?",
        fontsize=12, pad=8,
    )
    ax3.set_ylabel("Sharpe (annualized)")
    ax3.set_xlabel("Date")
    ax3.grid(True, alpha=0.3)
    locator = mdates.AutoDateLocator()
    ax3.xaxis.set_major_locator(locator)
    ax3.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    fig.text(
        0.99, 0.005,
        f"CAGR {kw['cagr']:+.2%}  |  Sharpe {kw['sharpe']:.2f}  |  "
        f"Sortino {kw['sortino']:.2f}  |  Calmar {kw['calmar']:.2f}  |  "
        f"max DD {dd_pct[worst_p]:.2f}%  |  underwater {kw['underwater_pct']:.0%}  |  "
        f"script: {script_label}",
        ha="right", fontsize=8.5, color="0.35",
    )
    fig.subplots_adjust(hspace=0.14, left=0.085, right=0.97, top=0.955, bottom=0.06)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
