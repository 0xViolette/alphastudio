"""Shared report content, terminal/HTML rendering, and trade-log export."""

from __future__ import annotations

import csv
import html
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from importlib import resources
from pathlib import Path
from string import Template

from .metrics import (
    Performance,
    benchmark_daily_returns,
    distribution_analysis,
    eoy_comparison,
    moments,
    rolling_analysis,
    worst_drawdowns,
)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    value: str
    note: str = ""
    sign: int | None = None
    sub: bool = False


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: list[Row]


@dataclass(frozen=True, slots=True)
class ReportContent:
    title: str
    subtitle: str
    cards: list[Row]
    overview: list[Row]
    sections: list[Section]
    benchmark: list[Row]
    monthly: dict
    annual: dict
    monthly_points: dict
    annual_points: dict
    extended: dict
    footer: str


def _polarity(value):
    try:
        if value > 0:
            return 1
        if value < 0:
            return -1
    except TypeError:
        pass
    return None


def _extended_report(performance):
    benchmark = benchmark_daily_returns(performance)
    initial = performance.metrics["initial"]

    def cumulative(values, log=False):
        output = []
        current = initial
        for value in values:
            current *= 1 + value
            output.append(math.log(current / initial) if log else current)
        return output

    _, strategy_sd, _, _ = moments(performance.returns)
    _, benchmark_sd, _, _ = moments(benchmark)
    volatility_scale = strategy_sd / benchmark_sd if benchmark_sd else 0.0
    return {
        "dailyTimes": [
            int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())
            for day in performance.days
        ],
        "rolling": rolling_analysis(performance),
        "eoy": eoy_comparison(performance),
        "worstDrawdowns": worst_drawdowns(performance),
        "distributions": distribution_analysis(performance),
        "logReturns": {
            "strategy": cumulative(performance.returns, log=True),
            "benchmark": cumulative(benchmark, log=True),
        },
        "volatilityMatched": {
            "strategy": performance.day_equity,
            "benchmark": cumulative(benchmark),
            "matched": cumulative([value * volatility_scale for value in benchmark]),
        },
    }


def build_report(performance: Performance) -> ReportContent:
    kw = performance.metrics
    trades = kw["trades"]
    overview = [
        Row("Period", f"{kw['d0']:%d %b %Y} → {kw['d1']:%d %b %Y}", f"{kw['years']:.2f} years"),
        Row("Initial balance", f"{kw['initial']:,.0f}", kw["initial_source"]),
        Row("Final equity (mark-to-market)", f"{kw['final']:,.0f}", f"realized {kw['final_realized']:,.0f}"),
        Row("Net P/L", f"{kw['pnl']:+,.0f}", f"open positions {kw['final_unrealized']:+,.0f}", _polarity(kw["pnl"])),
    ]
    returns = [
        Row("Total return", f"{kw['total']:+.2%}", "cumulative", _polarity(kw["total"])),
        Row("CAGR", f"{kw['cagr']:+.2%}", "compound annual growth rate", _polarity(kw["cagr"])),
        Row("Annualized return", f"{kw['ann_ret']:+.2%}", f"arithmetic mean × {kw['ppy']:.0f} sessions", _polarity(kw["ann_ret"])),
        Row("Annualized volatility", f"{kw['ann_vol']:.2%}", f"daily stdev × √{kw['ppy']:.0f}"),
        Row("Best session", f"{kw['best_day_r']:+.2%}", f"{kw['best_day']:%d %b %Y}", _polarity(kw["best_day_r"])),
        Row("Worst session", f"{kw['worst_day_r']:+.2%}", f"{kw['worst_day']:%d %b %Y}", _polarity(kw["worst_day_r"])),
        Row("Positive sessions", f"{kw['pos_days'] / kw['n_days']:.1%}", f"{kw['pos_days']} of {kw['n_days']}"),
        Row("Positive months", f"{kw['pos_months'] / len(kw['monthly']):.1%}", f"{kw['pos_months']} of {len(kw['monthly'])}"),
    ]
    risk = [
        Row("Sharpe ratio", f"{kw['sharpe']:.2f}", f"risk-free rate {kw['rf']:.2%}"),
        Row("Sortino ratio", f"{kw['sortino']:.2f}", f"minimum acceptable return {kw['mar']:.1%}"),
        Row("Calmar ratio", f"{kw['calmar']:.2f}", "CAGR / |maximum drawdown|"),
        Row("Omega ratio", f"{kw['omega']:.2f}", f"target {kw['mar']:.1%}"),
        Row("Ulcer index", f"{kw['ulcer']:.2f}%", "RMS of percentage drawdown"),
        Row("Ulcer performance index", f"{kw['upi']:.2f}", "return / Ulcer index"),
        Row("Recovery factor", f"{kw['recovery']:.2f}", "total return / maximum drawdown"),
    ]
    drawdown = [
        Row("Maximum drawdown", f"{kw['maxdd']:,.0f}", "account currency", _polarity(kw["maxdd"])),
        Row("Maximum drawdown %", f"{kw['maxdd_pct']:.2%}", "of prior equity peak", _polarity(kw["maxdd_pct"])),
        Row("Peak → trough", f"{kw['maxdd_peak']:%d %b %Y} → {kw['maxdd_trough']:%d %b %Y}", f"{kw['maxdd_len_days']} days"),
        Row("Recovered", kw["maxdd_rec"]),
        Row("Current drawdown", f"{kw['cur_dd']:.2%}", f"{kw['cur_days']} days underwater", _polarity(kw["cur_dd"])),
        Row("Average drawdown", f"{kw['avg_dd']:,.0f}", f"{kw['avg_dd_pct']:.2f}% across {kw['n_ep']} episodes"),
        Row("Longest drawdown", f"{kw['longest_days']} days", f"{kw['longest_fills']} bars below a high"),
        Row("Time underwater", f"{kw['underwater_pct']:.1%}", f"{kw['underwater_days']} of {kw['span_days']} days"),
    ]
    for index, depth in enumerate(kw["top5"], 1):
        percentage = depth / kw["peak_at_episode"][index - 1] * 100
        drawdown.append(Row(f"#{index} deepest", f"{depth:,.0f}", f"{percentage:.2f}% of episode peak", _polarity(depth), True))
    costs = [
        Row("Cost model", kw["cost_model"], "slippage is included in fill prices"),
        Row("Total fees", f"{kw['total_fees']:,.0f}", f"{kw['total_fees'] / trades['n']:,.0f} per closed trade" if trades["n"] else ""),
        Row("Fees / gross winning trades", f"{kw['total_fees'] / kw['gross_win']:.1%}" if kw["gross_win"] else "n/a"),
    ]
    trade_rows = [
        Row("Closed trades", f"{trades['n']:,}", f"{trades['n'] / kw['years']:.0f} per year"),
        Row("Winning closed trades", f"{trades['win_rate']:.1%}", f"{trades['wins']}W / {trades['losses']}L"),
        Row("Average win", f"{trades['avg_win']:+,.0f}", sign=_polarity(trades["avg_win"])),
        Row("Average loss", f"{trades['avg_loss']:+,.0f}", sign=_polarity(trades["avg_loss"])),
        Row("Payoff ratio", f"{trades['payoff']:.2f}", "average winning / losing closed trade"),
        Row("Expectancy per closed trade", f"{trades['expectancy']:+,.0f}", sign=_polarity(trades["expectancy"])),
        Row("Largest win", f"{trades['max_win']:+,.0f}" if trades["max_win"] is not None else "n/a", f"{kw['max_win_date']:%d %b %Y}" if kw["max_win_date"] else ""),
        Row("Largest loss", f"{trades['max_loss']:+,.0f}" if trades["max_loss"] is not None else "n/a", f"{kw['max_loss_date']:%d %b %Y}" if kw["max_loss_date"] else ""),
        Row("Longest win streak", f"{trades['max_win_streak']}", "consecutive closed trades"),
        Row("Longest loss streak", f"{trades['max_loss_streak']}", "consecutive closed trades"),
        Row("Average holding period", f"{trades['avg_hold_days']:.1f} days", f"{trades['avg_hold_bars']:.0f} bars"),
        Row("Time in market", f"{kw['exposure']:.1%}", "bars holding a position"),
        Row("Trade profit factor", f"{kw['profit_factor']:.2f}", "gross winning / losing closed-trade P&L"),
    ]
    distribution = [
        Row("VaR 95 (one session)", f"{kw['var95']:.2%}", "historical 5% left tail", _polarity(kw["var95"])),
        Row("VaR 99 (one session)", f"{kw['var99']:.2%}", "historical 1% left tail", _polarity(kw["var99"])),
        Row("CVaR 95", f"{kw['cvar95']:.2%}", "mean return beyond VaR 95", _polarity(kw["cvar95"])),
        Row("CVaR 99", f"{kw['cvar99']:.2%}", "mean return beyond VaR 99", _polarity(kw["cvar99"])),
        Row("Skewness", f"{kw['skew']:.2f}", "negative values indicate left-tail risk"),
        Row("Excess kurtosis", f"{kw['kurt'] - 3:.2f}", "positive values indicate fat tails"),
        Row("Tail ratio", f"{kw['tail']:.2f}", "|95th percentile| / |5th percentile|"),
        Row("Best / worst month", f"{kw['best_m']:+.2%} / {kw['worst_m']:+.2%}"),
    ]
    significance = [
        Row("Probabilistic Sharpe ratio", f"{kw['psr']:.1%}", "probability true Sharpe exceeds zero"),
        Row("Track record", f"{kw['n_days']:,} sessions", f"{kw['years']:.2f} years"),
        Row("Minimum track record for SR 1.0", "already met" if kw["mintrl"] == 0 else f"{kw['mintrl'] / kw['ppy']:.2f} years" if math.isfinite(kw["mintrl"]) else "not attainable at observed SR", "95% confidence"),
    ]
    if kw["trials"]:
        significance.extend((
            Row("Expected maximum Sharpe", f"{kw['emax']:.2f}", f"{kw['trials']:,} strategy trials"),
            Row("Deflated Sharpe ratio", f"{kw['dsr']:.1%}", "adjusted for multiple testing"),
        ))
    benchmark = []
    if kw["bench"]:
        bench = kw["bench"]
        benchmark = [
            Row("Buy & hold total return", f"{bench['total']:+.2%}", f"{bench['final']:,.0f} final equity", _polarity(bench["total"])),
            Row("Buy & hold CAGR", f"{bench['cagr']:+.2%}", sign=_polarity(bench["cagr"])),
            Row("Buy & hold max drawdown", f"{bench['maxdd']:.2%}"),
            Row("Buy & hold Sharpe", f"{bench['sharpe']:.2f}"),
            Row("Information ratio", f"{bench['ir']:.2f}", "active return / tracking error"),
            Row("Tracking error", f"{bench['te']:.2%}"),
            Row("Beta", f"{bench['beta']:.2f}"),
            Row("Correlation", f"{bench['corr']:.2f}", "daily returns"),
            Row("Jensen alpha", f"{bench['alpha']:+.2%}", "annualized, risk-free adjusted", _polarity(bench["alpha"])),
            Row("Up / down capture", f"{bench['up_cap']:.0%} / {bench['down_cap']:.0%}"),
        ]
    footer = (
        f"Equity is marked to market at each close. Return-based statistics use daily account returns; "
        f"trade statistics use FIFO closed round trips. Sharpe and Sortino annualize by √{kw['ppy']:.0f} sessions."
    )
    return ReportContent(
        title=f"{kw['strategy']} · {kw['data']}",
        subtitle=f"{kw['n_bars']:,} bars · {kw['n_days']:,} sessions · {kw['n_trades']:,} closed trades",
        cards=[
            Row("Final equity", f"{kw['final']:,.0f}", f"started at {kw['initial']:,.0f}"),
            Row("Net P/L", f"{kw['pnl']:+,.0f}", f"{kw['total']:+.2%} total", _polarity(kw["pnl"])),
            Row("CAGR", f"{kw['cagr']:+.2%}", f"{kw['years']:.2f} years", _polarity(kw["cagr"])),
            Row("Sharpe ratio", f"{kw['sharpe']:.2f}"),
            Row("Maximum drawdown", f"{kw['maxdd_pct']:.2%}", sign=_polarity(kw["maxdd_pct"])),
            Row("Closed-trade win rate", f"{trades['win_rate']:.1%}", f"{trades['wins']}W / {trades['losses']}L"),
            Row("Trade profit factor", f"{kw['profit_factor']:.2f}"),
            Row("Time underwater", f"{kw['underwater_pct']:.1%}"),
        ],
        overview=overview,
        sections=[
            Section("Returns", returns),
            Section("Risk-adjusted performance", risk),
            Section("Drawdown", drawdown),
            Section("Costs", costs),
            Section("Closed-trade statistics", trade_rows),
            Section("Return distribution", distribution),
            Section("Statistical significance", significance),
        ],
        benchmark=benchmark,
        monthly=kw["monthly"],
        annual=kw["annual"],
        monthly_points=kw["monthly_points"],
        annual_points=kw["annual_points"],
        extended=_extended_report(performance),
        footer=footer,
    )


def print_report(report: ReportContent) -> None:
    colored = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def style(text, code):
        return f"\033[{code}m{text}\033[0m" if colored else text

    def signed(value, sign):
        if not sign:
            return value
        return style(value, 32 if sign > 0 else 31)

    def print_row(row):
        value = signed(f"{row.value:>18}", row.sign)
        note = style(row.note, 2)
        print(f"  {row.label:<36} {value}  {note}")

    print(style(f"\n{report.title}", "1;36"))
    print(style(report.subtitle, 2))
    for row in report.overview:
        print_row(row)
    for section in report.sections:
        print(style(f"\n{section.title.upper()}", "1;36"))
        for row in section.rows:
            print_row(row)
    if report.benchmark:
        print(style("\nVERSUS BUY & HOLD", "1;36"))
        for row in report.benchmark:
            print_row(row)
    print(style("\nMONTHLY RETURNS", "1;36"))
    print(f"  {'Year':<6} " + " ".join(f"{month:>8}" for month in MONTHS) + f" {'Year':>9}")
    for year in sorted(report.annual):
        cells = []
        for month in range(1, 13):
            value = report.monthly.get((year, month))
            cells.append(
                signed(f"{value:+8.2%}", _polarity(value))
                if value is not None
                else " " * 8
            )
        annual = signed(f"{report.annual[year]:+9.2%}", _polarity(report.annual[year]))
        print(f"  {year:<6} " + " ".join(cells) + f" {annual}")
    print(style(f"\n{report.footer}\n", 2))


def _row_payload(row: Row) -> dict:
    return {
        "label": row.label,
        "value": row.value,
        "note": row.note,
        "sign": row.sign,
        "sub": row.sub,
    }


def _monthly_payload(monthly: dict, annual: dict) -> dict:
    return {
        "values": [
            {"year": year, "month": month, "value": value}
            for (year, month), value in sorted(monthly.items())
        ],
        "annual": [
            {"year": year, "value": value} for year, value in sorted(annual.items())
        ],
    }


def report_payload(report: ReportContent) -> dict:
    return {
        "title": report.title,
        "subtitle": report.subtitle,
        "cards": [_row_payload(row) for row in report.cards],
        "overview": [_row_payload(row) for row in report.overview],
        "sections": [
            {"title": section.title, "rows": [_row_payload(row) for row in section.rows]}
            for section in report.sections
        ],
        "benchmark": [_row_payload(row) for row in report.benchmark],
        "monthly": _monthly_payload(report.monthly, report.annual),
        "monthlyPoints": _monthly_payload(
            report.monthly_points, report.annual_points
        ),
        "extended": report.extended,
        "footer": report.footer,
    }


def chart_payload(result, *, include_candles=False) -> dict:
    """Always supply account curves; optionally supply the candle pane."""
    bars = result.chart_bars
    account = result.account
    initial = result.performance.metrics["initial"]

    def stamp(dt):
        return int(dt.replace(tzinfo=timezone.utc).timestamp())

    peak = initial
    drawdown = []
    for value in account.equity:
        peak = max(peak, value)
        drawdown.append((value / peak - 1) * 100 if peak else 0.0)
    payload = {
        "showCandles": include_candles,
        "times": [stamp(bar.dt) for bar in bars],
        "equity": account.equity,
        "drawdown": drawdown,
        "candles": [],
        "overlays": {},
        "markers": [],
        "fills": {},
    }
    if not include_candles:
        return payload

    lows = [bar.low for bar in bars if bar.low is not None]
    highs = [bar.high for bar in bars if bar.high is not None]
    padding = 0.1 * (max(highs) - min(lows))
    lower, upper = min(lows) - padding, max(highs) + padding
    probe_names = sorted({name for bar in bars for name in bar.probes})
    for name in probe_names:
        values = [bar.probes.get(name, math.nan) for bar in bars]
        present = [value for value in values if math.isfinite(value)]
        if present and min(present) >= lower and max(present) <= upper:
            payload["overlays"][name] = [
                value if math.isfinite(value) else None for value in values
            ]

    payload["candles"] = [
        [bar.open, bar.high, bar.low, bar.close] for bar in bars
    ]
    for bar in bars:
        for fill in bar.fills:
            timestamp = stamp(bar.dt)
            payload["markers"].append({
                "time": timestamp,
                "position": "belowBar" if fill.side > 0 else "aboveBar",
                "color": "#2563eb" if fill.side > 0 else "#d97706",
                "shape": "arrowUp" if fill.side > 0 else "arrowDown",
            })
            payload["fills"].setdefault(str(timestamp), []).append(
                f"{fill.tag or 'Fill'} · {fill.qty:g} units @ "
                f"{fill.price:,.2f} · fee {fill.fee:,.2f}"
            )
    return payload


def _json_script(data) -> str:
    return (
        json.dumps(data, allow_nan=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def write_html(path: Path, report: ReportContent, *, chart) -> None:
    """Assemble assets and data; all markup lives in templates/report.html."""
    package = resources.files("analytics")
    content = Template(package.joinpath("templates/report.html").read_text()).substitute(
        TITLE=html.escape(report.title),
        CSS=package.joinpath("assets/report.css").read_text(),
        JAVASCRIPT=package.joinpath("assets/report.js").read_text(),
        CHART_LIBRARY=package.joinpath("assets/lightweight-charts.js").read_text(),
        REPORT_DATA=_json_script(report_payload(report)),
        CHART_DATA=_json_script(chart),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_trade_log(path: Path, entries: list[dict]) -> None:
    """Write one common trade schema, extended for synthetic option execution."""
    path.parent.mkdir(parents=True, exist_ok=True)
    synthetic = any("option_legs" in entry for entry in entries)
    core = ["timestamp", "action", "order_id", "side", "quantity", "price", "fee"]
    extensions = [
        "contract", "is_rollover", "option_legs", "strike_price", "call_premium",
        "put_premium", "brokerage", "txn_fee", "sebi_fee", "stt", "stamp", "gst",
        "slippage_call", "slippage_put",
    ]
    fields = core + extensions if synthetic else core
    ist = timezone(timedelta(hours=5, minutes=30))
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for entry in entries:
            timestamp = entry["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=ist)
            else:
                timestamp = timestamp.astimezone(ist)
            breakup = entry.get("cost_breakup", {})
            row = {
                **entry,
                "timestamp": timestamp.isoformat(sep=" "),
                "order_id": entry.get("tag") or entry.get("action", ""),
                "quantity": entry.get("qty", entry.get("quantity", "")),
                "side": entry.get("side") or entry.get("futures_side", ""),
                "call_premium": breakup.get("call_premium", ""),
                "put_premium": breakup.get("put_premium", ""),
                "brokerage": breakup.get("brokerage", ""),
                "txn_fee": breakup.get("txn", ""),
                "sebi_fee": breakup.get("sebi", ""),
                "stt": breakup.get("stt", ""),
                "stamp": breakup.get("stamp", ""),
                "gst": breakup.get("gst", ""),
                "slippage_call": breakup.get("slippage_call", ""),
                "slippage_put": breakup.get("slippage_put", ""),
            }
            writer.writerow({field: row.get(field, "") for field in fields})
