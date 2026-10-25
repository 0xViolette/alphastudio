"""Candlestick chart of a strategy backtest (TradingView-style).

Renders the backtest's price series as an interactive candlestick chart with
trade markers and the strategy's probe overlays. HTML goes through the fast
canvas renderer (scripts/lwc.py, TradingView Lightweight Charts); plotly
(WebGL) is only used when --png asks for a static image.

Usage (from the strategy/ project root):
    python3 scripts/viz.py                          # run the backtest, render candles
    python3 scripts/viz.py --csv out.csv            # render a saved CSV stream
    python3 scripts/viz.py --from 2024-01-01 --till 2024-12-31

--from/--till (YYYY-MM-DD, IST; --till inclusive) restrict the backtest to a
date window; the interactive cost wizard names the range the data covers.
They do not apply to --csv (a saved stream is a finished backtest).

Data source: by default the emitter | dune exec pipeline of
scripts/plot_pnl.py is run; --csv PATH loads a saved CSV stream instead
(out.csv, scratch/out.csv, ...). The chart is written as self-contained HTML
(dark theme with an in-chart Dark/Light toggle); --png additionally writes a
static PNG via kaleido. --style light renders the PNG/light default.

--synthetic MODE (off|base|adverse|zero|all) additionally replays the same
signals through the downloaded ATM option pairs (scripts/synthetic.py) and
overlays magenta synthetic equity, dotted ATM-pair win/loss connectors and a
headline net/cagr in the footer; --csv streams cannot be replayed, so the two
flags are mutually exclusive. The TTY wizard asks for the mode, the adverse
spread multiplier and the margin estimate unless the --synthetic-* flags fix
them; non-TTY runs default to off. The option-store assumptions are printed
before the chart and repeated in scripts/tearsheet.py's report.
"""

import argparse
import csv
import io
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go

import costs as cost_cli
from plot_pnl import (
    DEFAULT_STRATEGY,
    SIDE_BY_TAG,
    STRATEGIES,
    resolve_strategy,
    run_backtest,
)

ROOT = Path(__file__).resolve().parent.parent

# Order specs are read from the strategy sources instead of being hardcoded
# per tag: every enter_*/exit_* declaration in bin/*.ml defines an order's
# action, long/short side and fill direction, and any (Stop <probe>) argument
# names the probe that carries the order's trigger level.
ORDER_SRC = re.compile(
    r'\b(enter_long|enter_short|exit_long|exit_short)\s+"([^"]+)"')
STOP_SRC = re.compile(r'\(\s*Stop\s+([A-Za-z_][A-Za-z0-9_]*)\b')
OCAML_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)


def _parse_order_specs():
    specs, index = {}, 0
    for path in sorted((ROOT / "strategies").glob("*.ml")):
        text = OCAML_COMMENT.sub(" ", path.read_text())
        for m in ORDER_SRC.finditer(text):
            action, tag = m.group(1), m.group(2)
            if tag in specs:
                continue
            entry = action.startswith("enter_")
            group = "long" if action.endswith("long") else "short"
            fill_side = 1 if action in ("enter_long", "exit_short") else -1
            end = text.find("\n", m.end())
            tail = text[m.end():] if end < 0 else text[m.end():end]
            stop = STOP_SRC.search(tail)
            specs[tag] = {
                "tag": tag, "action": action,
                "label": action.replace("_", " ").capitalize(),
                "entry": entry, "group": group,
                "fill_side": fill_side, "index": index,
                "stop": stop.group(1) if stop else None,
            }
            index += 1
    return specs


ORDER_SPECS = _parse_order_specs()


def fill_side(tag):
    """+1 for a buy fill, -1 for a sell fill, from bin/*.ml (then fallback)."""
    spec = ORDER_SPECS.get(tag)
    if spec is not None:
        return spec["fill_side"]
    if tag in SIDE_BY_TAG:
        return SIDE_BY_TAG[tag]
    sys.exit(f"unknown order tag {tag!r}; declare it with enter_long/"
             f"enter_short/exit_long/exit_short in bin/*.ml or add it to "
             f"SIDE_BY_TAG in scripts/plot_pnl.py")

# ---------------------------------------------------------------- themes
# TradingView palettes; gray #787b86 annotations stay readable on both.

THEMES = {
    "dark": {
        "bg": "#131722", "grid": "#1e222d", "axis": "#2a2e39",
        "font": "#d1d4dc", "up": "#26a69a", "down": "#ef5350",
        "line": "#2962ff", "hover_bg": "#1e222d", "hover_border": "#363c4e",
    },
    "light": {
        "bg": "#ffffff", "grid": "#f0f3fa", "axis": "#d6dae2",
        "font": "#131722", "up": "#089981", "down": "#f23645",
        "line": "#2962ff", "hover_bg": "#ffffff", "hover_border": "#d6dae2",
    },
}
NEUTRAL = "#787b86"  # annotation / watermark gray, readable on both themes
WIN, LOSS = "#26a69a", "#ef5350"
# fill-direction colors for markers; deliberately outside the candle palette
# (blue / magenta) so buys and sells never blend into green/red bars
BUY, SELL = "#2962ff", "#e040fb"

# probe overlay styling: conventional probe names keep their TradingView-ish
# colors, every other price-scale probe falls back to the cycle below
PROBE_STYLE = {
    "mov":     ("#f7a600", 1.4),
    "lx2n":    (NEUTRAL, 1),
    "sx2n":    (NEUTRAL, 1),
    "le":      ("#7986cb", 1),
    "se":      ("#5c6bc0", 1),
    "chand_l": ("#4db6ac", 1),
    "chand_s": ("#ff8a80", 1),
}
PROBE_FALLBACK = ("#42a5f5", "#ab47bc", "#26c6da", "#8d6e63",
                  "#ec407a", "#9ccc65", "#ffa726", "#5e35b1")


def probe_style(name, i=0):
    """(color, width) for a probe overlay, stable across a CSV's columns."""
    if name in PROBE_STYLE:
        return PROBE_STYLE[name]
    return PROBE_FALLBACK[i % len(PROBE_FALLBACK)], 1


def price_probes(ctx):
    """The probe columns that live on the price scale.

    Any probe whose values sit inside the candle range (moving averages, stop
    levels, trailing stops, ...) is an overlay; values on other scales (ATR,
    position size, ...) are left out. This is how viz overlays the probes of
    an arbitrary strategy without knowing any of its names.
    """
    lo, hi = min(ctx["low"]), max(ctx["high"])
    pad = 0.1 * (hi - lo)
    out = []
    for name, series in ctx["probes"].items():
        vals = [v for v in series if not math.isnan(v)]
        if vals and min(vals) >= lo - pad and max(vals) <= hi + pad:
            out.append(name)
    return out


def axis_names(fig):
    """Every xaxis/yaxis attribute name present on the figure's layout."""
    return [k for k in fig.layout if k.startswith(("xaxis", "yaxis"))]


def theme_relayout(theme, axes):
    """Flat relayout dict that re-skins a figure to `theme` (for the toggle)."""
    t = THEMES[theme]
    d = {
        "paper_bgcolor": t["bg"],
        "plot_bgcolor": t["bg"],
        "font.color": t["font"],
        "hoverlabel.bgcolor": t["hover_bg"],
        "hoverlabel.bordercolor": t["hover_border"],
        "hoverlabel.font.color": t["font"],
    }
    for ax in axes:
        d[f"{ax}.gridcolor"] = t["grid"]
        d[f"{ax}.zerolinecolor"] = t["axis"]
        d[f"{ax}.linecolor"] = t["axis"]
    return d


def apply_theme(fig, theme, stats_line=""):
    """Skin `fig` and attach the Dark/Light toggle buttons."""
    axes = axis_names(fig)
    fig.update_layout(
        **{k.replace("__", "."): v for k, v in
           theme_relayout(theme, axes).items()},
        font=dict(family="Inter, -apple-system, Helvetica, Arial, sans-serif",
                  size=12),
        hovermode="x unified",
        hoverdistance=30,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        margin=dict(l=70, r=60, t=110, b=72),
    )
    # keep the title clear of the preset / theme button rows
    fig.update_layout(title=dict(yref="container", y=0.945, yanchor="top"))
    fig.update_layout(
        updatemenus=list(fig.layout.updatemenus or []) + [dict(
            type="buttons", direction="right", showactive=False,
            x=1.0, xanchor="right", y=1.13, yanchor="top",
            pad=dict(r=2, t=2), bgcolor=THEMES[theme]["grid"],
            bordercolor=THEMES[theme]["axis"], font=dict(size=11),
            buttons=[
                dict(label="Dark", method="relayout",
                     args=[theme_relayout("dark", axes)]),
                dict(label="Light", method="relayout",
                     args=[theme_relayout("light", axes)]),
            ],
        )],
    )
    if stats_line:
        fig.add_annotation(
            text=stats_line, xref="paper", yref="paper", x=1, y=-0.062,
            showarrow=False, xanchor="right", font=dict(size=11, color=NEUTRAL),
        )
    return fig


def add_preset_buttons(fig, theme):
    """Toolbar row of visibility presets (All / Trades / Stops / Bare).

    Matches traces by legend name, so it works regardless of how many probe
    overlays were actually present in the data.
    """
    names = [tr.name for tr in fig.data]
    core = {"Up", "Down", "close"}  # webgl candles + hover helper
    fills = {"Long entry", "Short entry", "Exit fill"}
    connectors = {"Winning trades", "Losing trades"}
    stops = set(names) - core - fills - connectors  # the probe overlays
    presets = [
        ("All", set(names)),
        ("Trades", core | fills | connectors),
        ("Stops", core | fills | stops),
        ("Bare", core),
    ]
    buttons = [
        dict(label=label, method="restyle",
             args=[{"visible": [n in visible for n in names]}])
        for label, visible in presets
    ]
    fig.update_layout(
        updatemenus=list(fig.layout.updatemenus or []) + [dict(
            type="buttons", direction="right", showactive=False,
            x=0.0, xanchor="left", y=1.13, yanchor="top",
            pad=dict(r=2, t=2), bgcolor=THEMES[theme]["grid"],
            bordercolor=THEMES[theme]["axis"], font=dict(size=11),
            buttons=buttons,
        )],
    )


# ---------------------------------------------------------------- data layer


def parse_stream(text):
    """Backtest CSV stream -> (dates, ohlc, probes, per-bar fills with tags)."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    if not rows:
        sys.exit("empty data stream")
    header = None
    for r in rows:
        if r and "Date" in r and "event" in r:
            header = r
            rows = rows[rows.index(r):]
            break
    if header is None:
        sys.exit("empty data stream")
    i_dt, i_tm = header.index("Date"), header.index("Time")
    i_o, i_h, i_l, i_c = (header.index(c) for c in ("Open", "High", "Low", "Close"))
    i_evt = header.index("event")
    i_px, i_qt = header.index("fill_price"), header.index("fill_qty")
    i_ct = header.index("fill_cost") if "fill_cost" in header else None
    probe_names = header[i_c + 1:i_evt]

    dates, opens, highs, lows, closes = [], [], [], [], []
    probes = {n: [] for n in probe_names}
    bar_fills = []  # per bar: [(side, price, qty, tag, fee), ...]
    nan = float("nan")
    for row in rows[1:]:
        if not row:
            continue
        dates.append(datetime.strptime(row[i_dt] + " " + row[i_tm],
                                       "%m/%d/%y %I:%M %p"))
        opens.append(float(row[i_o]))
        highs.append(float(row[i_h]))
        lows.append(float(row[i_l]))
        closes.append(float(row[i_c]))
        for j, name in enumerate(probe_names):
            cell = row[i_c + 1 + j]
            probes[name].append(float(cell) if cell else nan)
        fills = []
        events = [e.strip() for e in row[i_evt].split("|")
                  if e.strip().endswith("filled")]
        if events:
            prices, qtys = row[i_px].split("|"), row[i_qt].split("|")
            costs = row[i_ct].split("|") if i_ct is not None else ["0"] * len(events)
            for event, price, qty, cost in zip(events, prices, qtys, costs):
                tag = event[: -len("filled")].strip()
                fills.append((fill_side(tag), float(price), float(qty), tag,
                              float(cost or 0)))
        bar_fills.append(fills)
    return dates, opens, highs, lows, closes, probes, bar_fills


def is_zero(x):
    return abs(x) < 1e-7


def sgn(x):
    return 0 if is_zero(x) else (1 if x > 0 else -1)


def ledger(dates, closes, bar_fills, lot_size, initial):
    """FIFO lot ledger over the bars.

    Returns (equity, net_lots, realized, trades): equity is mark-to-market,
    trades are rich round trips with entry/exit bar index, datetime, price,
    direction, size and pnl — everything the chart needs for markers and
    connectors.
    """
    realized, lots = 0.0, []
    equity, net, realized_series, trades = [], [], [], []
    for i, (dt, close, fills) in enumerate(zip(dates, closes, bar_fills)):
        for side, price, qty, tag, fee in fills:
            realized -= fee
            this_fpl = fee / qty if qty else 0.0
            remaining = side * qty
            while lots and not is_zero(remaining) \
                    and sgn(lots[0][1]) == -sgn(remaining):
                entry_price, lot_qty, entry_i, entry_fpl = lots[0]
                lots = lots[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                pnl = direction * closed * lot_size * (price - entry_price)
                realized += pnl
                trades.append({
                    "dir": "long" if direction > 0 else "short",
                    "lots": closed,
                    "pnl": pnl - closed * (entry_fpl + this_fpl),
                    "entry_i": entry_i, "entry_dt": dates[entry_i],
                    "entry_px": entry_price,
                    "exit_i": i, "exit_dt": dt, "exit_px": price,
                    "exit_tag": tag,
                })
                left = lot_qty - direction * closed
                if not is_zero(left):
                    lots = [(entry_price, left, entry_i, entry_fpl)] + lots
                remaining += direction * closed
            if not is_zero(remaining):
                lots.append((price, remaining, i, this_fpl))
        unrealized = sum(sgn(q) * abs(q) * lot_size * (close - p)
                         for p, q, _, _ in lots)
        equity.append(initial + realized + unrealized)
        realized_series.append(realized)
        net.append(sum(q for _, q, _, _ in lots))
    return equity, net, realized_series, trades


def load_data(args, cfg):
    """Shared context the chart builder consumes."""
    if args.csv:
        text = Path(args.csv).read_text()
        data_name = Path(args.csv).name
    else:
        text, err = run_backtest(STRATEGIES[args.strategy], cfg.argv())
        data_name = "backtest"
        if "filled" not in text:
            sys.exit(f"no fills found in strategy output; stderr was:\n{err}")
    initial = cfg.initial_balance
    lot_size = cfg.lot_size
    dates, o, h, l, c, probes, bar_fills = parse_stream(text)
    equity, net, realized, trades = ledger(dates, c, bar_fills, lot_size, initial)
    if not trades:
        print("warning: no round-trip trades found in the data",
              file=sys.stderr)
    return {
        "dates": dates, "open": o, "high": h, "low": l, "close": c,
        "probes": probes, "bar_fills": bar_fills,
        "equity": equity, "net": net, "realized": realized, "trades": trades,
        "initial": initial, "lot_size": lot_size, "costs": cfg.label(),
        "strategy": args.strategy, "data": data_name, "args": args,
    }


# ---------------------------------------------------------------- chart helpers


def index_axis(dates):
    """Gapless integer x-axis (TradingView collapses session gaps the same
    way): returns (xs, tickvals, ticktext, hover date strings)."""
    n = len(dates)
    xs = list(range(n))
    step = max(1, n // 12)
    tickvals = xs[::step]
    ticktext = [f"{dates[i]:%d %b %y}" for i in tickvals]
    datestr = [f"{d:%a %d %b %y %H:%M}" for d in dates]
    return xs, tickvals, ticktext, datestr


def set_index_ticks(fig, tickvals, ticktext, axis="xaxis"):
    fig.update_layout(**{axis: dict(tickmode="array", tickvals=tickvals,
                                    ticktext=ticktext)})


# WebGL rendering: tens of thousands of candlesticks as SVG nodes freeze the
# browser, so price charts are drawn as Scattergl subpaths (wick line +
# filled body rectangle per candle, None-separated), which the GPU handles.


def _split_by_direction(o, c):
    for oo, cc in zip(o, c):
        yield cc >= oo


def webgl_candles(fig, xs, o, h, l, c, theme, row=None, col=None):
    """Candlesticks as two WebGL traces (up / down)."""
    kw = dict(row=row, col=col) if row else {}
    up, dn = {"x": [], "y": []}, {"x": [], "y": []}
    for x, oo, hh, ll, cc, is_up in zip(xs, o, h, l, c,
                                        _split_by_direction(o, c)):
        tgt = up if is_up else dn
        w = 0.35
        top, bot = max(oo, cc), min(oo, cc)
        tgt["x"] += [x, x, None, x - w, x + w, x + w, x - w, x - w, None]
        tgt["y"] += [hh, ll, None, top, top, bot, bot, top, None]
    t = THEMES[theme]
    for seg, color, name in ((up, t["up"], "Up"), (dn, t["down"], "Down")):
        fig.add_trace(go.Scattergl(
            x=seg["x"], y=seg["y"], mode="lines", fill="toself",
            fillcolor=color, line=dict(color=color, width=1),
            hoverinfo="skip", name=name, showlegend=False,
        ), **kw)


def add_hover_helper(fig, xs, ohlc, datestr, row=None, col=None):
    """Invisible close line that feeds the unified hover with the bar's full
    OHLC (TradingView crosshair style)."""
    kw = dict(row=row, col=col) if row else {}
    o, h, l, c = ohlc
    fig.add_trace(go.Scattergl(
        x=xs, y=c, mode="lines", line=dict(width=0), opacity=0,
        hovertext=datestr, customdata=list(zip(o, h, l)),
        hovertemplate=("%{hovertext}<br>"
                       "O %{customdata[0]:,.2f}  H %{customdata[1]:,.2f}"
                       "<br>L %{customdata[2]:,.2f}  C %{y:,.2f}"),
        name="close", showlegend=False,
    ), **kw)


# what each order tag does (for the fill hover); fill side comes from
# plot_pnl.SIDE_BY_TAG
TAG_ACTION = {
    "LE": "Enter long", "SE": "Enter short",
    "2NL": "Exit long · 2N stop", "TRL": "Exit long · chandelier",
    "SE cover": "Exit long · reverse stop",
    "2NS": "Exit short · 2N stop", "TRS": "Exit short · chandelier",
    "LE cover": "Exit short · reverse stop",
    "TP_L": "Exit long · take profit", "SL_L": "Exit long · stop loss",
    "TS_L": "Exit long · trailing stop",
    "TP_S": "Exit short · take profit", "SL_S": "Exit short · stop loss",
    "TS_S": "Exit short · trailing stop",
}


def stop_level(ctx, tag, i):
    """The trigger level of the order that produced this fill.

    The level comes from the probe named in the order's (Stop <probe>)
    argument in bin/*.ml, so this works for any strategy. Timing
    (lib/backtest.ml): the strategy evaluates on bar t's close and its orders
    rest into bar t+1, whose range they fill against — so the order behind a
    fill on bar i was placed at the close of bar i-1 with the level it had
    then.
    """
    j = i - 1
    if j < 0:
        return None
    spec = ORDER_SPECS.get(tag)
    series = ctx["probes"].get(spec["stop"]) if spec else None
    if not series:
        return None
    lvl = series[j]
    if math.isnan(lvl):
        return None
    return lvl


def add_trade_markers(fig, ctx, xs, row=None, col=None, connectors=True):
    """TradingView-style trade markers.

    Entry fills (enter_* orders) are triangles beyond the bar's wick — buys
    below the low, sells above the high; exit fills are small circles at the
    fill price. Round trips are joined by dotted connectors colored by PnL.
    The hover of every fill states the order's trigger level (the probe in
    its (Stop <probe>) argument, placed at the prior bar's close), the fill
    price and the slippage between them.
    """
    kw = dict(row=row, col=col) if row else {}
    dates, atr = ctx["dates"], ctx["probes"].get("atr", [])
    long_e = {"x": [], "y": [], "text": []}
    short_e = {"x": [], "y": [], "text": []}
    exits = {"x": [], "y": [], "text": [], "color": []}
    for i, fills in enumerate(ctx["bar_fills"]):
        a = atr[i] if i < len(atr) and not math.isnan(atr[i]) else None
        for side, price, qty, tag, fee in fills:
            spec = ORDER_SPECS.get(tag)
            stop = stop_level(ctx, tag, i)
            action = TAG_ACTION.get(tag, tag)
            text = f"{action} · {qty:g} lots"
            if fee:
                text += f" · fee {fee:,.0f}"
            if stop is not None:
                slip = side * (price - stop)
                text += (f"<br>{tag} stop placed "
                         f"{dates[i - 1]:%d %b %y %H:%M} @ {stop:,.2f}"
                         f"<br>filled {dates[i]:%d %b %y %H:%M} @ {price:,.2f}")
                if abs(slip) > 1e-9:
                    text += f" · slip {slip:+.2f}"
            else:
                text += f"<br>{tag} filled {dates[i]:%d %b %y %H:%M} @ {price:,.2f}"
            off = 0.5 * a if a else 0.005 * price
            entry = spec["entry"] if spec else "enter" in action.lower()
            if entry:
                tgt = long_e if side > 0 else short_e
                y = ctx["low"][i] - off if side > 0 else ctx["high"][i] + off
            else:
                tgt, y = exits, price
                exits["color"].append(BUY if side > 0 else SELL)
            tgt["x"].append(xs[i])
            tgt["y"].append(y)
            tgt["text"].append(text)
    fig.add_trace(go.Scattergl(
        x=long_e["x"], y=long_e["y"], text=long_e["text"], mode="markers",
        name="Long entry", hoverinfo="text",
        marker=dict(symbol="triangle-up", size=11, color=BUY,
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    fig.add_trace(go.Scattergl(
        x=short_e["x"], y=short_e["y"], text=short_e["text"], mode="markers",
        name="Short entry", hoverinfo="text",
        marker=dict(symbol="triangle-down", size=11, color=SELL,
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    fig.add_trace(go.Scattergl(
        x=exits["x"], y=exits["y"], text=exits["text"], mode="markers",
        name="Exit fill", hoverinfo="text",
        marker=dict(symbol="circle", size=6, color=exits["color"],
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    if connectors:
        for won in (True, False):
            cx, cy = [], []
            for t in ctx["trades"]:
                if (t["pnl"] > 0) != won:
                    continue
                cx += [xs[t["entry_i"]], xs[t["exit_i"]], None]
                cy += [t["entry_px"], t["exit_px"], None]
            fig.add_trace(go.Scattergl(
                x=cx, y=cy, mode="lines", hoverinfo="skip",
                name="Winning trades" if won else "Losing trades",
                line=dict(color=WIN if won else LOSS, width=1.2),
                opacity=0.55,
            ), **kw)



def probe_overlays(fig, ctx, xs, row=None, col=None):
    """Every price-scale probe of the strategy as a thin line.

    Names come straight from the CSV header (the strategy's probe list), so
    an arbitrary strategy is overlaid without viz knowing its indicators.
    """
    kw = dict(row=row, col=col) if row else {}
    for i, name in enumerate(price_probes(ctx)):
        series = ctx["probes"][name]
        color, width = probe_style(name, i)
        ys = [None if math.isnan(v) else v for v in series]
        fig.add_trace(go.Scattergl(
            x=xs, y=ys, mode="lines", name=name, hoverinfo="y+name",
            line=dict(color=color, width=width),
        ), **kw)


def stats_footer(ctx):
    """One-line watermark: CAGR / Sharpe / maxDD on the mark-to-market curve."""
    eq, dates, initial = ctx["equity"], ctx["dates"], ctx["initial"]
    day_eq = {}
    for d, e in zip(dates, eq):
        day_eq[d.date()] = e
    days = sorted(day_eq)
    if len(days) < 2:
        return ""
    vals = [day_eq[d] for d in days]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    years = (days[-1] - days[0]).days / 365.25 or 1e-9
    ppy = len(days) / years
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1))
    sharpe = mean / sd * math.sqrt(ppy) if sd else float("nan")
    cagr = (vals[-1] / initial) ** (1 / years) - 1
    peak, maxdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1)
    line = (f"CAGR {cagr:+.1%}   Sharpe {sharpe:.2f}   "
            f"max DD {maxdd:.1%}   trades {len(ctx['trades'])}")
    return line


def base_title(ctx, what):
    return (f"{what} — {ctx['strategy']} on {ctx['data']} · "
            f"{ctx['costs']} · {len(ctx['dates']):,} bars")


# ---------------------------------------------------------------- price chart


def make_candles(ctx, theme):
    xs, tv, tt, ds = index_axis(ctx["dates"])
    fig = go.Figure()
    webgl_candles(fig, xs, ctx["open"], ctx["high"], ctx["low"],
                  ctx["close"], theme)
    add_hover_helper(fig, xs, (ctx["open"], ctx["high"], ctx["low"],
                               ctx["close"]), ds)
    probe_overlays(fig, ctx, xs)
    add_trade_markers(fig, ctx, xs)
    add_preset_buttons(fig, theme)
    set_index_ticks(fig, tv, tt)
    fig.update_layout(title=dict(text=base_title(ctx, "Candlesticks"),
                                 font=dict(size=16)),
                      yaxis_title="Price")
    return fig


# ---------------------------------------------------------------- render


def render(ctx, theme, out_base, want_png):
    label = "Candlesticks + trade markers"
    html = out_base.with_suffix(".html")
    # candles gets the fast canvas renderer (scripts/lwc.py, TradingView
    # Lightweight Charts); plotly is only used for the optional PNG
    import lwc

    lwc.render_candles(ctx, theme, html)
    print(f"{label}\n  html: {html}")
    if want_png:
        fig = make_candles(ctx, theme)
        apply_theme(fig, theme, stats_footer(ctx))
        _write_png(fig, out_base.with_suffix(".png"))
        print(f"  png:  {out_base.with_suffix('.png')}")


def _write_png(fig, png):
    # direct kaleido call: plotly's write_image() gives no way to raise the
    # 90 s render timeout, which big WebGL charts can exceed
    import kaleido

    kaleido.write_fig_sync(
        fig.to_dict(), path=str(png),
        opts=dict(format="png", width=1680, height=940, scale=2),
        kopts={"timeout": 300},
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", choices=list(STRATEGIES), default=None,
                    help=f"which bin/ strategy to run "
                         f"(default: {DEFAULT_STRATEGY}; prompts on a TTY "
                         "when omitted)")
    ap.add_argument("--csv", type=Path,
                    help="load a saved backtest CSV stream instead of running dune")
    ap.add_argument("--out", type=Path,
                    help="output path stem (default: candles.html in project root)")
    ap.add_argument("--png", action="store_true",
                    help="also write a static PNG via kaleido")
    ap.add_argument("--style", choices=list(THEMES), default="dark",
                    help="theme at render time (HTML also gets a Dark/Light toggle)")
    cost_cli.add_arguments(ap)
    args = ap.parse_args()
    if args.csv and (args.from_date or args.till_date):
        ap.error("--from/--till only apply when running the backtest; "
                 "drop --csv to window the data")
    # with --csv no backtest runs: the strategy name is only a chart label,
    # so fall back to the default without prompting
    args.strategy = resolve_strategy(args.strategy, prompt=args.csv is None)

    cfg = cost_cli.resolve(args, prompt=args.csv is None, ask_period=False)
    ctx = load_data(args, cfg)
    render(ctx, args.style, args.out or (ROOT / "candles"), args.png)


if __name__ == "__main__":
    main()
