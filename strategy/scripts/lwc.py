"""Fast candles chart via TradingView's Lightweight Charts (canvas-based).

plotly (even WebGL) tops out around a few thousand candles before pan/zoom
get sluggish; Lightweight Charts is the canvas engine TradingView itself
ships, built for 100k+ bars. This module renders the candles chart as one
self-contained HTML file: the vendored library (scripts/vendor/
lightweight-charts.js) and all series data are inlined, so it opens offline.

Used by scripts/viz.py for the `candles` chart only; every other chart type
still goes through plotly. The candles view also carries a toggleable
lines/orders legend (every price-scale probe plus per-order-ID fill toggles),
mark-to-market net equity and an underwater drawdown pane.
"""

import json
import math
from datetime import timezone
from pathlib import Path

from viz import (ORDER_SPECS, TAG_ACTION, fill_side, price_probes,
                   probe_style, stats_footer)

VENDOR = Path(__file__).resolve().parent / "vendor" / "lightweight-charts.js"
CDN = ("https://unpkg.com/lightweight-charts@5.2.1/dist/"
       "lightweight-charts.standalone.production.js")

# Fill colors: one tight shade family per book \u2014 cool blues for long,
# warm oranges for short \u2014 so the side of every fill is obvious from the
# color family alone, while each ID keeps its own shade. Position matches the
# role order declared in bin/*.ml (entry, stop, cover, trail on both sides).
LONG_COLORS = ("#2962ff", "#00b0ff", "#64b5f6", "#00b8d4")
SHORT_COLORS = ("#ff9100", "#ffca28", "#ff4081", "#ff5252")


def _round(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) \
        else round(v, 2)


def _unix(dt):
    # naive wall-clock carried as-if-UTC so the chart shows the CSV's times
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _order_meta(ctx):
    """One legend entry per order ID that actually filled, colored by book.

    IDs/actions/sides come from the enter_*/exit_* declarations in bin/*.ml
    (via viz.ORDER_SPECS); anything only present in the CSV is classified from
    TAG_ACTION or its fill direction. Entries sort first within each side.
    """
    counts = {}
    for bar_fills in ctx["bar_fills"]:
        for _, _, _, tag, _fee in bar_fills:
            counts[tag] = counts.get(tag, 0) + 1

    orders = []
    for tag, count in counts.items():
        spec = ORDER_SPECS.get(tag)
        if spec:
            action = TAG_ACTION.get(tag, spec["label"])
            entry, group, index = spec["entry"], spec["group"], spec["index"]
        else:
            action = TAG_ACTION.get(tag, tag)
            entry = "enter" in action.lower()
            words = f"{tag} {action}".lower()
            group = ("long" if "long" in words else
                     "short" if "short" in words else
                     "long" if fill_side(tag) > 0 else "short")
            index = 10_000 + len(orders)
        orders.append({"id": tag, "action": action, "entry": entry,
                       "group": group, "index": index, "count": count})

    orders.sort(key=lambda o: (o["group"] != "long", not o["entry"],
                               o["index"], o["id"]))
    # stable ID -> shade: rank the declared orders of each book by their
    # bin/*.ml index, so a tag keeps its color even if some IDs never fill
    rank = {}
    for grp in ("long", "short"):
        known = sorted(ORDER_SPECS[t]["index"] for t in ORDER_SPECS
                       if ORDER_SPECS[t]["group"] == grp)
        rank[grp] = {ix: n for n, ix in enumerate(known)}
    extra = {"long": 0, "short": 0}
    for o in orders:
        spec = ORDER_SPECS.get(o["id"])
        if spec is not None:
            n = rank[o["group"]][spec["index"]]
        else:
            n = len(rank[o["group"]]) + extra[o["group"]]
            extra[o["group"]] += 1
        palette = LONG_COLORS if o["group"] == "long" else SHORT_COLORS
        o["color"] = palette[n % len(palette)]
        del o["index"]
    return orders


def build_payload(ctx):
    """Everything the JS needs, as compact JSON-able structures."""
    dates = ctx["dates"]
    times = [_unix(d) for d in dates]

    candles = [[_round(v) for v in bar] for bar in
               zip(ctx["open"], ctx["high"], ctx["low"], ctx["close"])]

    overlays, lines = {}, []
    for i, name in enumerate(price_probes(ctx)):
        overlays[name] = [_round(v) for v in ctx["probes"][name]]
        color, width = probe_style(name, i)
        lines.append({"name": name, "color": color, "width": width})

    orders = _order_meta(ctx)
    meta = {o["id"]: o for o in orders}
    markers, fills = [], {}
    for i, bar_fills in enumerate(ctx["bar_fills"]):
        for side, price, qty, tag, _fee in bar_fills:
            o = meta[tag]
            if o["entry"]:
                position = "atPriceBottom" if o["group"] == "long" \
                    else "atPriceTop"
                shape = "arrowUp" if o["group"] == "long" else "arrowDown"
            else:
                # exits sit at the exact fill price, so a reversal pair on the
                # same bar can never hide one marker behind the other
                position, shape = "atPriceMiddle", "circle"
            markers.append({
                "time": times[i], "position": position,
                "price": _round(price), "color": o["color"],
                "shape": shape, "size": 1, "id": tag,
            })
            dot = f'<span style="color:{o["color"]}">\u25a0</span>'
            fills.setdefault(times[i], []).append(
                [tag, f'{dot} <b>{tag}</b> {o["action"].lower()} \u00b7 '
                      f'{qty:g} lots @ {price:,.2f}'])

    equity = ctx["equity"]
    realized = ctx["realized"]
    initial = ctx["initial"]
    unreal = [e - initial - r for e, r in zip(equity, realized)]
    peak, drawdown = initial, []
    for e in equity:
        peak = max(peak, e)
        drawdown.append((e / peak - 1) * 100 if peak else 0.0)

    def segments(won):
        segs = []
        for t in ctx["trades"]:
            if (t["pnl"] > 0) != won:
                continue
            segs.append([[times[t["entry_i"]], round(t["entry_px"], 2)],
                         [times[t["exit_i"]], round(t["exit_px"], 2)]])
        return segs

    # flatten segments to a strictly-ascending [time, value|null] stream;
    # null breaks the line between trades (1 s bumps keep times unique)
    connectors = {}
    for key, won in (("win", True), ("loss", False)):
        flat, last = [], -1
        for (t1, p1), (t2, p2) in segments(won):
            t1, t2 = max(t1, last + 1), max(t2, last + 2)
            flat.append([t1, p1])
            flat.append([t2, p2])
            flat.append([t2 + 1, None])
            last = t2 + 1
        connectors[key] = flat

    return {
        "times": times, "candles": candles, "overlays": overlays,
        "lines": lines,
        "orders": orders, "markers": markers, "fills": fills,
        "connectors": connectors,
        "equity": [_round(v) for v in equity],
        "realized": [_round(v) for v in realized],
        "unreal": [_round(v) for v in unreal],
        "drawdown": [round(v, 4) for v in drawdown],
        "net": [round(v, 2) for v in ctx["net"]],
    }


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#131722; --grid:#1e222d; --border:#2a2e39;
    --font:#d1d4dc; --up:#26a69a; --down:#ef5350; --muted:#787b86; }
  html, body { margin: 0; height: 100%; font-family: Inter, -apple-system,
    Helvetica, Arial, sans-serif; background: var(--bg); color: var(--font); }
  body { display: flex; flex-direction: column; }
  #toolbar { display: flex; align-items: center; gap: 6px; padding: 6px 10px;
    font-size: 12px; user-select: none; border-bottom: 1px solid var(--border); }
  #toolbar .spacer { flex: 1; }
  #toolbar .title { font-size: 14px; font-weight: 600; margin-right: 12px; }
  #toolbar button { font: inherit; padding: 3px 10px; border-radius: 4px;
    border: 1px solid var(--border); background: var(--grid);
    color: var(--font); cursor: pointer; }
  #toolbar button.on { font-weight: 700;
    box-shadow: inset 0 0 0 2px currentColor; }
  #main { flex: 1; display: flex; min-height: 0; }
  #legend { width: 214px; overflow-y: auto; padding: 6px 0;
    border-right: 1px solid var(--border); font-size: 11.5px; }
  .legend-head { padding: 7px 10px 3px; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: .04em; }
  .legend-head .links { float: right; }
  .legend-head .links a { margin-left: 7px; cursor: pointer; color: #2962ff;
    font-weight: 600; font-size: 10px; text-transform: none;
    letter-spacing: 0; }
  .legend-head .links a:hover { text-decoration: underline; }
  .legend-head .sub { display: block; color: var(--muted); font-weight: 400;
    font-size: 9.5px; text-transform: none; letter-spacing: 0; }
  #legend .group { padding: 6px 10px 2px; color: var(--muted);
    font-size: 9.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .08em; }
  #legend .row { display: flex; align-items: center; gap: 6px;
    padding: 3px 10px; cursor: pointer; user-select: none; }
  #legend .row:hover, #legend .row.spot { background: var(--grid); }
  #legend .row.off { opacity: .32; }
  #legend .row.off .tag, #legend .row.off .linename {
    text-decoration: line-through; }
  #legend .glyph { width: 12px; text-align: center; font-size: 10px; }
  #legend .lineswatch { width: 14px; height: 0; flex: none;
    border-top: 2px solid currentColor; }
  #legend .lineswatch.dotted { border-top-style: dotted; }
  #legend .tag { font-weight: 600; min-width: 46px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  #legend .linename { flex: 1; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
  #legend .act { flex: 1; color: var(--muted); font-size: 10.5px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #legend .cnt { color: var(--muted); font-variant-numeric: tabular-nums; }
  #chart { flex: 1; position: relative; min-width: 0; }
  #info { position: absolute; top: 8px; left: 10px; z-index: 3;
    font-size: 12px; line-height: 1.55; pointer-events: none;
    white-space: pre-wrap; max-width: 62%;
    text-shadow: 0 0 4px var(--bg), 0 0 4px var(--bg); }
  #info .mut { color: var(--muted); }
  .pane-label { position: absolute; left: 10px; z-index: 2;
    pointer-events: none; color: var(--muted); font-size: 9px;
    font-weight: 700; letter-spacing: .09em; text-transform: uppercase;
    background: var(--bg); padding: 0 3px; border-radius: 2px; }
  #footer { padding: 3px 10px; font-size: 11px; text-align: right;
    color: var(--muted); }
</style>
</head>
<body>
<div id="toolbar">
  <span class="title">__TITLE__</span>
  <span id="presets"></span>
  <span class="spacer"></span>
  <span id="legendbtn"></span>
  <span id="themes"></span>
</div>
<div id="main">
  <aside id="legend">
    <div class="legend-head"><span class="links">
        <a id="lines-all" title="Show every line">all</a>
        <a id="lines-none" title="Hide every line">none</a></span>
      Lines
      <span class="sub">click to hide a line</span></div>
    <div id="lines"></div>
    <div class="legend-head"><span class="links">
        <a id="orders-all" title="Show every order ID">all</a>
        <a id="orders-none" title="Hide every order ID">none</a></span>
      Orders
      <span class="sub">hover to spotlight · click to hide</span></div>
    <div id="orders"></div>
  </aside>
  <div id="chart"><div id="info"></div></div>
</div>
<div id="footer">__FOOTER__</div>
__LIB__
<script>
"use strict";
const TIMES = __TIMES__;
const CANDLES = __CANDLES__;
const OVERLAYS = __OVERLAYS__;
const LINES = __LINES__;
const ORDERS = __ORDERS__;
const MARKERS = __MARKERS__;
const CONNECTORS = __CONNECTORS__;
const FILLS = __FILLS__;
const EQUITY = __EQUITY__;
const REALIZED = __REALIZED__;
const UNREAL = __UNREAL__;
const DRAWDOWN = __DRAWDOWN__;
const NET = __NET__;

// order-fill and overlay visibility state, shared by the custom primitive
const hidden = new Set();
const FILL_STATE = { enabled: true, spotlight: null, bg: "#131722" };

const THEMES = {
  dark:  {bg:"#131722", grid:"#1e222d", border:"#2a2e39", font:"#d1d4dc",
          up:"#26a69a", down:"#ef5350"},
  light: {bg:"#ffffff", grid:"#f0f3fa", border:"#d6dae2", font:"#131722",
          up:"#089981", down:"#f23645"},
};
const WIN = "#26a69a", LOSS = "#ef5350", GRAY = "#787b86";
const NET_COLOR = "#2962ff", REAL_COLOR = "#26a69a", UNREAL_COLOR = "#f7a600";
const DD_COLOR = "#ef5350";
const CONNECTOR_STYLES = {
  win:  {color: WIN,  name: "Winning trades", dotted: true,
         desc: "Dotted round-trip connectors"},
  loss: {color: LOSS, name: "Losing trades", dotted: true,
         desc: "Dotted round-trip connectors"},
};

const fmtAxisMoney = v => {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(2)}`;
};
const fmtMoney = v => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(
  "en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const fmtNum = v => v == null ? "\u2013" : v.toLocaleString("en-US",
  {minimumFractionDigits: 2, maximumFractionDigits: 2});
const fmtLots = v => Number.isInteger(v) ? String(v) : v.toFixed(2);
const fmtTime = t => {
  const d = new Date(t * 1000);
  const p = n => String(n).padStart(2, "0");
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct",
             "Nov","Dec"];
  return `${p(d.getUTCDate())} ${M[d.getUTCMonth()]} ` +
         `${String(d.getUTCFullYear()).slice(2)} ${p(d.getUTCHours())}:` +
         `${p(d.getUTCMinutes())}`;
};

const chartEl = document.getElementById("chart");
const infoEl = document.getElementById("info");
const ordersEl = document.getElementById("orders");
const linesEl = document.getElementById("lines");
const legendEl = document.getElementById("legend");
const chart = LightweightCharts.createChart(chartEl, {
  autoSize: true,
  layout: { attributionLogo: false,
            panes: { enableResize: true } },
  timeScale: { timeVisible: true, secondsVisible: false,
               borderVisible: false, minBarSpacing: 0.001 },
  rightPriceScale: { borderVisible: false },
  leftPriceScale: { visible: false },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  grid: { vertLines: { visible: true }, horzLines: { visible: true } },
});

const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
  borderVisible: false, priceLineVisible: false,
});
candleSeries.setData(TIMES.map((t, i) => ({
  time: t, open: CANDLES[i][0], high: CANDLES[i][1],
  low: CANDLES[i][2], close: CANDLES[i][3],
})));
candleSeries.priceScale().applyOptions({
  scaleMargins: { top: 0.03, bottom: 0.03 },
});

const overlaySeries = {};
for (const ln of LINES) {
  const series = OVERLAYS[ln.name];
  const s = chart.addSeries(LightweightCharts.LineSeries, {
    color: ln.color, lineWidth: ln.width, priceLineVisible: false,
    lastValueVisible: false, crosshairMarkerVisible: false,
  });
  s.setData(TIMES.map((t, i) => series[i] == null
    ? { time: t } : { time: t, value: series[i] }));
  overlaySeries[ln.name] = s;
}

const connectorSeries = {};
for (const key of ["win", "loss"]) {
  const s = chart.addSeries(LightweightCharts.LineSeries, {
    color: key === "win" ? WIN : LOSS, lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
  s.setData(CONNECTORS[key].map(p => p[1] == null
    ? { time: p[0] } : { time: p[0], value: p[1] }));
  connectorSeries[key] = s;
}

// ---- fill markers (custom primitive: outlined, dimmable) --------------
class FillMarkersRenderer {
  draw(target) {
    if (!FILL_STATE.enabled) return;
    const ts = chart.timeScale();
    const spot = hidden.has(FILL_STATE.spotlight) ? null : FILL_STATE.spotlight;
    const bs = (ts.options && ts.options().barSpacing) || 6;
    const scale = Math.max(1, Math.min(2.6, bs / 4));
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const { width, height } = scope.mediaSize;
      ctx.save();
      try {
        ctx.lineJoin = "round";
        for (const m of MARKERS) {
          if (hidden.has(m.id)) continue;
          const x = ts.timeToCoordinate(m.time);
          const y = candleSeries.priceToCoordinate(m.price);
          if (x === null || y === null ||
              x < -8 || x > width + 8 || y < -12 || y > height + 12) continue;
          const dim = spot !== null && spot !== m.id;
          const focused = spot === m.id;
          ctx.globalAlpha = dim ? 0.1 : 1;
          ctx.fillStyle = m.color;
          ctx.strokeStyle = FILL_STATE.bg;
          ctx.lineWidth = (dim ? 1 : 1.4) * Math.min(scale, 1.7);
          if (m.shape === "circle") {
            ctx.beginPath();
            ctx.arc(x, y, (focused ? 4.4 : 3.0) * scale, 0, Math.PI * 2);
            ctx.fill();
            if (!dim) ctx.stroke();
          } else {
            const up = m.shape === "arrowUp";
            const s = (focused ? 7 : 5) * scale;
            const t = 1 * scale;
            ctx.beginPath();
            if (up) {
              ctx.moveTo(x, y - t);
              ctx.lineTo(x - s, y + s);
              ctx.lineTo(x + s, y + s);
            } else {
              ctx.moveTo(x, y + t);
              ctx.lineTo(x - s, y - s);
              ctx.lineTo(x + s, y - s);
            }
            ctx.closePath();
            ctx.fill();
            if (!dim) ctx.stroke();
          }
        }
      } finally {
        ctx.globalAlpha = 1;
        ctx.restore();
      }
    });
  }
}
class FillMarkersView {
  constructor() { this._renderer = new FillMarkersRenderer(); }
  zOrder() { return "top"; }
  renderer() { return this._renderer; }
}
class FillMarkersPrimitive {
  constructor() { this._view = new FillMarkersView(); this._requestUpdate = null; }
  attached(p) { this._requestUpdate = p.requestUpdate; }
  detached() { this._requestUpdate = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  refresh() { if (this._requestUpdate) this._requestUpdate(); }
}
const fillPrimitive = new FillMarkersPrimitive();
candleSeries.attachPrimitive(fillPrimitive);

// ---- equity / P&L / drawdown panes -------------------------------------
chart.addPane();
const netSeries = chart.addSeries(LightweightCharts.AreaSeries, {
  lineColor: NET_COLOR, lineWidth: 2, title: "Net",
  topColor: "rgba(41, 98, 255, 0.22)",
  bottomColor: "rgba(41, 98, 255, 0.02)",
  priceFormat: { type: "custom", formatter: fmtAxisMoney },
  priceLineVisible: false, lastValueVisible: true,
  crosshairMarkerVisible: false,
}, 1);
netSeries.setData(TIMES.map((t, i) => ({ time: t, value: EQUITY[i] })));

chart.addPane();
const ddSeries = chart.addSeries(LightweightCharts.AreaSeries, {
  lineColor: DD_COLOR, lineWidth: 1,
  topColor: "rgba(239, 83, 80, 0.28)",
  bottomColor: "rgba(239, 83, 80, 0.02)",
  title: "DD",
  priceFormat: { type: "percent", precision: 1 },
  priceLineVisible: false, lastValueVisible: true,
  crosshairMarkerVisible: false,
}, 2);
ddSeries.setData(TIMES.map((t, i) => ({ time: t, value: DRAWDOWN[i] })));
ddSeries.createPriceLine({
  price: 0, color: GRAY, lineWidth: 1,
  lineStyle: LightweightCharts.LineStyle.Dashed,
  axisLabelVisible: false, title: "",
});

chart.panes()[0].setStretchFactor(3.2);
chart.panes()[1].setStretchFactor(1.0);
chart.panes()[2].setStretchFactor(0.7);

// pane captions (the price scales only have room for short series titles)
const paneCaptions = [
  null,
  'NET EQUITY',
  'DRAWDOWN \u00b7 FROM PEAK',
];
const paneLabelEls = [];
for (let i = 1; i < paneCaptions.length; i++) {
  const el = document.createElement("div");
  el.className = "pane-label";
  el.innerHTML = paneCaptions[i];
  chartEl.appendChild(el);
  paneLabelEls[i] = el;
}
function layoutPaneLabels() {
  let top = 0;
  for (let i = 0; i < paneLabelEls.length; i++) {
    if (paneLabelEls[i]) paneLabelEls[i].style.top = `${top + 5}px`;
    if (i < chart.panes().length) top += chart.paneSize(i).height + 1;
  }
}
const paneResize = new ResizeObserver(layoutPaneLabels);
paneResize.observe(chartEl);
requestAnimationFrame(() => {
  for (const pane of chart.panes()) {
    const el = pane.getHTMLElement();
    if (el) paneResize.observe(el);
  }
});

// ---- info panel: OHLC, fills and account state at the hovered bar ------
const timeIndex = new Map(TIMES.map((t, i) => [t, i]));

function setInfo(i) {
  if (i == null) i = TIMES.length - 1;
  const [o, h, l, c] = CANDLES[i];
  const up = c >= o;
  const col = up ? "var(--up)" : "var(--down)";
  let html = `${fmtTime(TIMES[i])}  ` +
    `O <span style="color:${col}">${fmtNum(o)}</span> ` +
    `H <span style="color:${col}">${fmtNum(h)}</span> ` +
    `L <span style="color:${col}">${fmtNum(l)}</span> ` +
    `C <span style="color:${col}">${fmtNum(c)}</span>`;
  html += `<br><span class="mut">Net equity</span> ` +
    `<span style="color:${NET_COLOR}">${fmtMoney(EQUITY[i])}</span>` +
    `  <span class="mut">Drawdown</span> ` +
    `<span style="color:${DD_COLOR}">${DRAWDOWN[i].toFixed(2)}%</span>`;
  const pos = NET[i];
  html += `<br><span class="mut">Realized</span> ` +
    `<span style="color:${REAL_COLOR}">${fmtMoney(REALIZED[i])}</span>` +
    `  <span class="mut">Unrealized</span> ` +
    `<span style="color:${UNREAL_COLOR}">${fmtMoney(UNREAL[i])}</span>` +
    `  <span class="mut">Position</span> ` +
    `${pos > 0 ? "+" : ""}${fmtLots(pos)} lots`;
  const f = FILLS[TIMES[i]];
  if (f) {
    const shown = f.filter(([id]) => !hidden.has(id)).map(([, text]) => text);
    if (shown.length) html += "<br>" + shown.join("<br>");
  }
  infoEl.innerHTML = html;
}
setInfo(null);
chart.subscribeCrosshairMove(param => {
  if (!param.time) { setInfo(null); return; }
  const i = timeIndex.get(param.time);
  setInfo(i == null ? null : i);
});

// ---- line + order legends ----------------------------------------------
function renderMarkers() { fillPrimitive.refresh(); }

const lineState = {}, lineRows = {};
function addLineRow(key, name, color, dotted, desc) {
  lineState[key] = true;
  const row = document.createElement("div");
  row.className = "row";
  row.title = desc;
  const sw = document.createElement("span");
  sw.className = "lineswatch" + (dotted ? " dotted" : "");
  sw.style.color = color;
  const label = document.createElement("span");
  label.className = "linename";
  label.textContent = name;
  row.append(sw, label);
  row.onclick = () => {
    lineState[key] = !lineState[key];
    applyLineState();
    markCustomPreset();
  };
  lineRows[key] = row;
  linesEl.appendChild(row);
}
function buildLineRows() {
  for (const ln of LINES)
    addLineRow(ln.name, ln.name, ln.color, false,
               `strategy probe \u00b7 ${ln.name}`);
  for (const key of ["win", "loss"]) {
    if (!CONNECTORS[key].length) continue;
    const st = CONNECTOR_STYLES[key];
    addLineRow(key, st.name, st.color, st.dotted, st.desc);
  }
}
function applyLineState() {
  for (const key of Object.keys(overlaySeries))
    overlaySeries[key].applyOptions({ visible: !!lineState[key] });
  for (const key of Object.keys(connectorSeries))
    connectorSeries[key].applyOptions({ visible: !!lineState[key] });
  for (const key of Object.keys(lineRows))
    lineRows[key].classList.toggle("off", !lineState[key]);
}
function setAllLines(on) {
  for (const key of Object.keys(lineState)) lineState[key] = on;
  applyLineState();
  markCustomPreset();
}
buildLineRows();
document.getElementById("lines-all").onclick = () => setAllLines(true);
document.getElementById("lines-none").onclick = () => setAllLines(false);

function buildOrderRows() {
  for (const [grp, label] of [["long", "Long"], ["short", "Short"]]) {
    const rows = ORDERS.filter(o => o.group === grp);
    if (!rows.length) continue;
    const head = document.createElement("div");
    head.className = "group";
    head.textContent = label;
    ordersEl.appendChild(head);
    for (const o of rows) {
      const row = document.createElement("div");
      row.className = "row" + (hidden.has(o.id) ? " off" : "");
      row.title = `${o.action} \u00b7 ${o.count} fills`;
      const glyph = document.createElement("span");
      glyph.className = "glyph";
      glyph.textContent = o.entry ? (grp === "long" ? "\\u25b2" : "\\u25bc")
                                  : "\\u25cf";
      glyph.style.color = o.color;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = o.id;
      const act = document.createElement("span");
      act.className = "act";
      const bits = o.action.split("\\u00b7");
      act.textContent = o.entry ? grp : (bits[1] || o.action).trim();
      const cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = o.count;
      row.append(glyph, tag, act, cnt);
      row.dataset.id = o.id;
      row.onmouseenter = () => {
        FILL_STATE.spotlight = o.id;
        row.classList.add("spot");
        renderMarkers();
      };
      row.onmouseleave = () => {
        FILL_STATE.spotlight = null;
        row.classList.remove("spot");
        renderMarkers();
      };
      row.onclick = () => {
        if (hidden.has(o.id)) {
          hidden.delete(o.id);
          row.classList.remove("off");
        } else {
          hidden.add(o.id);
          row.classList.add("off");
        }
        renderMarkers();
      };
      ordersEl.appendChild(row);
    }
  }
}
buildOrderRows();
function setAllOrders(hide) {
  hidden.clear();
  if (hide) for (const o of ORDERS) hidden.add(o.id);
  for (const row of ordersEl.querySelectorAll(".row"))
    row.classList.toggle("off", hidden.has(row.dataset.id));
  renderMarkers();
}
document.getElementById("orders-all").onclick = () => setAllOrders(false);
document.getElementById("orders-none").onclick = () => setAllOrders(true);

const legendBtnEl = document.getElementById("legendbtn");
const legendBtn = document.createElement("button");
legendBtn.textContent = "Legend";
legendBtn.title = "Toggle the order legend";
legendBtnEl.appendChild(legendBtn);
let legendOn = true;
function setLegendOn(on) {
  legendOn = on;
  legendEl.style.display = on ? "block" : "none";
  legendBtn.classList.toggle("on", on);
}
legendBtn.onclick = () => setLegendOn(!legendOn);

// ---- presets + theme toggle --------------------------------------------
const PRESETS = {
  all:    { overlays: true,  markers: true,  connectors: true  },
  trades: { overlays: false, markers: true,  connectors: true  },
  stops:  { overlays: true, markers: true, connectors: false },
  bare:   { overlays: false, markers: false, connectors: false },
};
function applyPreset(name) {
  const p = PRESETS[name];
  for (const key of Object.keys(lineState)) {
    if (key === "win" || key === "loss")
      lineState[key] = p.connectors === true;
    else lineState[key] = p.overlays === true;
  }
  applyLineState();
  FILL_STATE.enabled = p.markers;
  renderMarkers();
  for (const b of document.querySelectorAll("#presets button"))
    b.classList.toggle("on", b.dataset.preset === name);
}
function markCustomPreset() {
  for (const b of document.querySelectorAll("#presets button"))
    b.classList.remove("on");
}
const presetsEl = document.getElementById("presets");
for (const name of Object.keys(PRESETS)) {
  const b = document.createElement("button");
  b.textContent = name[0].toUpperCase() + name.slice(1);
  b.dataset.preset = name;
  b.onclick = () => applyPreset(name);
  presetsEl.appendChild(b);
}

function applyTheme(name) {
  const t = THEMES[name];
  chart.applyOptions({
    layout: { background: { color: t.bg }, textColor: t.font,
              panes: { separatorColor: t.border } },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
  });
  candleSeries.applyOptions({
    upColor: t.up, downColor: t.down,
    wickUpColor: t.up, wickDownColor: t.down,
  });
  const root = document.documentElement.style;
  root.setProperty("--bg", t.bg);
  root.setProperty("--grid", t.grid);
  root.setProperty("--border", t.border);
  root.setProperty("--font", t.font);
  root.setProperty("--up", t.up);
  root.setProperty("--down", t.down);
  document.body.style.background = t.bg;
  document.body.style.color = t.font;
  FILL_STATE.bg = t.bg;
  fillPrimitive.refresh();
  for (const b of document.querySelectorAll("#themes button"))
    b.classList.toggle("on", b.dataset.theme === name);
}
const themesEl = document.getElementById("themes");
for (const name of ["dark", "light"]) {
  const b = document.createElement("button");
  b.textContent = name[0].toUpperCase() + name.slice(1);
  b.dataset.theme = name;
  b.onclick = () => applyTheme(name);
  themesEl.appendChild(b);
}

applyTheme("__THEME__");
applyPreset("all");
chart.timeScale().fitContent();
requestAnimationFrame(() => requestAnimationFrame(layoutPaneLabels));

// allow ?theme=light&preset=stops&legend=0 in the URL fragment for deep links
const h = new URLSearchParams(location.hash.slice(1));
if (h.get("theme")) applyTheme(h.get("theme"));
if (h.get("preset")) applyPreset(h.get("preset"));
if (h.get("legend") === "0") setLegendOn(false);
</script>
</body>
</html>
"""


def render_candles(ctx, theme, out_path):
    """Write the self-contained Lightweight Charts candles HTML."""
    payload = build_payload(ctx)
    dumps = lambda o: json.dumps(o, separators=(",", ":"))
    if VENDOR.exists():
        lib_tag = "<script>\n" + VENDOR.read_text() + "\n</script>"
    else:
        lib_tag = f'<script src="{CDN}"></script>'
    html = HTML_TEMPLATE
    html = html.replace("__TITLE__",
                        f"candles — {ctx['strategy']} on {ctx['data']} · "
                        f"{len(ctx['dates']):,} bars")
    html = html.replace("__FOOTER__", stats_footer(ctx))
    html = html.replace("__LIB__", lib_tag)
    html = html.replace("__TIMES__", dumps(payload["times"]))
    html = html.replace("__CANDLES__", dumps(payload["candles"]))
    html = html.replace("__OVERLAYS__", dumps(payload["overlays"]))
    html = html.replace("__LINES__", dumps(payload["lines"]))
    html = html.replace("__ORDERS__", dumps(payload["orders"]))
    html = html.replace("__MARKERS__", dumps(payload["markers"]))
    html = html.replace("__CONNECTORS__", dumps(payload["connectors"]))
    html = html.replace("__FILLS__", dumps(payload["fills"]))
    html = html.replace("__EQUITY__", dumps(payload["equity"]))
    html = html.replace("__REALIZED__", dumps(payload["realized"]))
    html = html.replace("__UNREAL__", dumps(payload["unreal"]))
    html = html.replace("__DRAWDOWN__", dumps(payload["drawdown"]))
    html = html.replace("__NET__", dumps(payload["net"]))
    html = html.replace("__THEME__", theme)
    Path(out_path).write_text(html)
