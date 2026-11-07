(() => {
  const root = document.documentElement;
  const report = JSON.parse(document.getElementById("report-data").textContent);
  const chartData = JSON.parse(document.getElementById("chart-data").textContent);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let chart = null;
  let candleSeries = null;
  const charts = [];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function tone(sign) {
    return sign > 0 ? "positive" : sign < 0 ? "negative" : "";
  }

  function renderRows(tbody, rows) {
    for (const row of rows) {
      const tr = element("tr", row.sub ? "sub" : "");
      tr.appendChild(element("th", "", row.label));
      tr.appendChild(element("td", `value ${tone(row.sign)}`, row.value));
      tr.appendChild(element("td", "note", row.note));
      tbody.appendChild(tr);
    }
  }

  function renderCards() {
    const container = document.getElementById("summary-cards");
    for (const card of report.cards) {
      const item = element("article", "card");
      item.appendChild(element("div", "card-label", card.label));
      item.appendChild(element("div", `card-value ${tone(card.sign)}`, card.value));
      item.appendChild(element("div", "card-note", card.note));
      container.appendChild(item);
    }
  }

  function renderSection(section) {
    const panel = element("section", "panel");
    panel.appendChild(element("h2", "", section.title));
    const table = element("table", "stats");
    const tbody = element("tbody");
    renderRows(tbody, section.rows);
    table.appendChild(tbody);
    panel.appendChild(table);
    return panel;
  }

  function heat(cell, value, maximum) {
    if (value === 0) return;
    const strength = 0.08 + 0.38 * Math.min(Math.abs(value) / maximum, 1);
    cell.style.backgroundColor = value > 0
      ? `rgba(16,185,129,${strength})`
      : `rgba(239,68,68,${strength})`;
  }

  function renderMonthly(id, data, points) {
    if (!data.annual.length) return;
    document.getElementById(id).hidden = false;
    const header = element("tr");
    header.appendChild(element("th", "", "Year"));
    for (const month of months) header.appendChild(element("th", "", month));
    header.appendChild(element("th", "", "Total"));
    document.getElementById(`${id}-head`).appendChild(header);

    const values = new Map(data.values.map(item => [`${item.year}-${item.month}`, item.value]));
    const totals = new Map(data.annual.map(item => [item.year, item.value]));
    const maximum = Math.max(...data.values.map(item => Math.abs(item.value)), 1e-9);
    const body = document.getElementById(`${id}-body`);
    for (const [year, total] of totals) {
      const row = element("tr");
      row.appendChild(element("th", "", year));
      for (let month = 1; month <= 12; month += 1) {
        const value = values.get(`${year}-${month}`);
        const text = value === undefined ? "·" : points ? value.toFixed(2) : `${(value * 100).toFixed(2)}%`;
        const cell = element("td", value === undefined ? "muted" : tone(value), text);
        if (value !== undefined) heat(cell, value, maximum);
        row.appendChild(cell);
      }
      row.appendChild(element("td", tone(total), points ? total.toFixed(2) : `${(total * 100).toFixed(2)}%`));
      body.appendChild(row);
    }
  }

  function renderTable(id, headers, rows) {
    const table = document.getElementById(id);
    const thead = element("thead");
    const header = element("tr");
    for (const label of headers) header.appendChild(element("th", "", label));
    thead.appendChild(header);
    const tbody = element("tbody");
    for (const values of rows) {
      const row = element("tr");
      for (const value of values) row.appendChild(element("td", "", value));
      tbody.appendChild(row);
    }
    table.replaceChildren(thead, tbody);
  }

  function lineChart(id, series, percentage = false) {
    const available = series.some(item => item.values.some(value => value !== null));
    if (!available) return false;
    const colors = palette();
    const mini = LightweightCharts.createChart(document.getElementById(id), {
      autoSize: true,
      layout: { background: { color: colors.background }, textColor: colors.text, attributionLogo: false },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      timeScale: { timeVisible: false, secondsVisible: false },
      rightPriceScale: { borderColor: colors.grid },
    });
    charts.push(mini);
    for (const item of series) {
      const line = mini.addSeries(LightweightCharts.LineSeries, {
        color: item.color,
        lineWidth: 1.5,
        priceLineVisible: false,
        title: item.name,
        priceFormat: percentage ? { type: "custom", formatter: value => `${value.toFixed(1)}%` } : undefined,
      });
      line.setData(report.extended.dailyTimes.map((time, i) => (
        item.values[i] === null ? { time } : { time, value: item.values[i] }
      )));
    }
    mini.timeScale().fitContent();
    return true;
  }

  function svgElement(tag, attributes, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderDistribution(id, bins) {
    const svg = document.getElementById(id);
    svg.setAttribute("viewBox", "0 0 800 260");
    if (!bins.length) return;
    const xMin = bins[0].x;
    const xMax = bins[bins.length - 1].x;
    const xSpan = xMax - xMin || 1;
    const yMax = Math.max(...bins.flatMap(bin => [bin.count, bin.density]), 1e-9);
    const x = value => 55 + (value - xMin) / xSpan * 720;
    const y = value => 225 - value / yMax * 205;
    const width = Math.max(2, 700 / bins.length * 0.8);
    for (const bin of bins) {
      svg.appendChild(svgElement("rect", {
        x: x(bin.x) - width / 2,
        y: y(bin.count),
        width,
        height: 225 - y(bin.count),
        fill: "rgba(37,99,235,.38)",
      }));
    }
    const path = bins.map((bin, index) => `${index ? "L" : "M"}${x(bin.x)},${y(bin.density)}`).join(" ");
    svg.appendChild(svgElement("path", { d: path, fill: "none", stroke: "#dc2626", "stroke-width": 2 }));
    svg.appendChild(svgElement("line", { x1: 45, y1: 225, x2: 785, y2: 225, stroke: "currentColor", opacity: 0.25 }));
    svg.appendChild(svgElement("text", { x: 45, y: 248, fill: "currentColor" }, `${(xMin * 100).toFixed(2)}%`));
    svg.appendChild(svgElement("text", { x: 730, y: 248, fill: "currentColor" }, `${(xMax * 100).toFixed(2)}%`));
    svg.appendChild(svgElement("text", { x: 8, y: 18, fill: "currentColor" }, `n=${yMax.toFixed(1)}`));
  }

  function renderExtended() {
    const extended = report.extended;
    const percent = value => `${(value * 100).toFixed(2)}%`;
    const hasBenchmark = extended.eoy.some(row => row.benchmark !== null);
    renderTable(
      "eoy-table",
      hasBenchmark ? ["Year", "Strategy", "Benchmark", "Multiplier", "Won"] : ["Year", "Strategy"],
      extended.eoy.map(row => hasBenchmark
        ? [row.year, percent(row.strategy), percent(row.benchmark), `${row.multiplier.toFixed(2)}×`, row.won ? "Yes" : "No"]
        : [row.year, percent(row.strategy)]),
    );
    if (extended.worstDrawdowns.length) {
      document.getElementById("worst-drawdowns").hidden = false;
      renderTable(
        "drawdown-table",
        ["Started", "Trough", "Recovered", "Drawdown", "Days"],
        extended.worstDrawdowns.map(row => [row.start, row.trough, row.recovered, percent(row.depth), row.days]),
      );
    }
    const rolling = extended.rolling;
    const rollingVisible = [
      lineChart("rolling-sharpe", [{ name: "Sharpe", color: "#2563eb", values: rolling.sharpe }]),
      lineChart("rolling-sortino", [{ name: "Sortino", color: "#9333ea", values: rolling.sortino }]),
      lineChart("rolling-volatility", [{ name: "Volatility", color: "#ea580c", values: rolling.volatility }], true),
      lineChart("rolling-beta", [{ name: "Beta", color: "#65a30d", values: rolling.beta }]),
    ].some(Boolean);
    document.getElementById("rolling-section").hidden = !rollingVisible;
    lineChart("log-returns", [
      { name: "Strategy", color: "#2563eb", values: extended.logReturns.strategy },
      { name: "Benchmark", color: "#64748b", values: extended.logReturns.benchmark },
    ], true);
    if (extended.volatilityMatched.benchmark.length) {
      document.getElementById("volatility-section").hidden = false;
      lineChart("volatility-returns", [
        { name: "Strategy", color: "#2563eb", values: extended.volatilityMatched.strategy },
        { name: "Benchmark", color: "#64748b", values: extended.volatilityMatched.benchmark },
        { name: "Volatility matched", color: "#9333ea", values: extended.volatilityMatched.matched },
      ]);
    }
    renderDistribution("daily-distribution", extended.distributions.daily);
    renderDistribution("monthly-distribution", extended.distributions.monthly);
  }

  document.getElementById("report-title").textContent = report.title;
  document.getElementById("report-subtitle").textContent = report.subtitle;
  document.getElementById("report-footer").textContent = report.footer;
  renderCards();
  renderRows(document.getElementById("overview"), report.overview);
  const sections = document.getElementById("sections");
  for (const section of report.sections) sections.appendChild(renderSection(section));
  if (report.benchmark.length) {
    document.getElementById("benchmark").hidden = false;
    renderRows(document.getElementById("benchmark-rows"), report.benchmark);
  }
  renderMonthly("monthly", report.monthly, false);
  renderMonthly("monthly-points", report.monthlyPoints, true);
  renderExtended();

  function palette() {
    const dark = root.dataset.theme === "dark";
    return {
      text: dark ? "#fafafa" : "#0f172a",
      grid: dark ? "#27272a" : "#e2e8f0",
      background: dark ? "#18181b" : "#ffffff",
      up: dark ? "#34d399" : "#047857",
      down: dark ? "#f87171" : "#dc2626",
    };
  }

  function applyTheme() {
    const dark = root.dataset.theme === "dark";
    document.getElementById("theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
    const colors = palette();
    for (const item of charts) {
      item.applyOptions({
        layout: { background: { color: colors.background }, textColor: colors.text },
        grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      });
    }
    if (candleSeries) {
      candleSeries.applyOptions({
        upColor: colors.up, downColor: colors.down,
        wickUpColor: colors.up, wickDownColor: colors.down,
      });
    }
  }

  document.getElementById("theme-toggle").addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme();
  });

  const colors = palette();
  chart = LightweightCharts.createChart(document.getElementById("chart"), {
    autoSize: true,
    layout: { background: { color: colors.background }, textColor: colors.text, attributionLogo: false },
    grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: colors.grid },
  });
  charts.push(chart);

  let equityPane = 0;
  if (chartData.showCandles) {
    document.getElementById("chart-title").textContent = "Price, fills, equity, and underwater curve";
    candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: colors.up, downColor: colors.down,
      wickUpColor: colors.up, wickDownColor: colors.down,
      priceLineVisible: false,
    });
    candleSeries.setData(chartData.times.map((time, i) => ({
      time,
      open: chartData.candles[i][0],
      high: chartData.candles[i][1],
      low: chartData.candles[i][2],
      close: chartData.candles[i][3],
    })));
    const overlayColors = ["#2563eb", "#9333ea", "#ea580c", "#65a30d"];
    Object.entries(chartData.overlays).forEach(([name, values], index) => {
      const series = chart.addSeries(LightweightCharts.LineSeries, {
        color: overlayColors[index % overlayColors.length],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: name,
      });
      series.setData(chartData.times.map((time, i) => (
        values[i] === null ? { time } : { time, value: values[i] }
      )));
    });
    LightweightCharts.createSeriesMarkers(candleSeries, chartData.markers);
    chart.addPane();
    equityPane = 1;
  }

  const equity = chart.addSeries(LightweightCharts.AreaSeries, {
    lineColor: "#2563eb", topColor: "rgba(37,99,235,.18)",
    bottomColor: "rgba(37,99,235,.02)", priceLineVisible: false, title: "Equity",
  }, equityPane);
  equity.setData(chartData.times.map((time, i) => ({ time, value: chartData.equity[i] })));
  chart.addPane();
  const drawdownPane = equityPane + 1;
  const drawdown = chart.addSeries(LightweightCharts.AreaSeries, {
    lineColor: "#dc2626", topColor: "rgba(220,38,38,.18)",
    bottomColor: "rgba(220,38,38,.02)", priceLineVisible: false, title: "Underwater %",
  }, drawdownPane);
  drawdown.setData(chartData.times.map((time, i) => ({ time, value: chartData.drawdown[i] })));
  if (chartData.showCandles) {
    chart.panes()[0].setStretchFactor(3);
    chart.panes()[1].setStretchFactor(1);
    chart.panes()[2].setStretchFactor(1);
  } else {
    chart.panes()[0].setStretchFactor(2);
    chart.panes()[1].setStretchFactor(1);
  }

  const info = document.getElementById("chart-info");
  const indices = new Map(chartData.times.map((time, index) => [time, index]));
  function showBar(index) {
    if (index === undefined) index = chartData.times.length - 1;
    const date = new Date(chartData.times[index] * 1000).toUTCString().slice(5, 22);
    const lines = [date];
    if (chartData.showCandles) {
      const candle = chartData.candles[index];
      lines.push(`O ${candle[0].toFixed(2)}  H ${candle[1].toFixed(2)}  L ${candle[2].toFixed(2)}  C ${candle[3].toFixed(2)}`);
    }
    lines.push(`Equity ${chartData.equity[index].toFixed(2)}   Underwater ${chartData.drawdown[index].toFixed(2)}%`);
    const fills = chartData.fills[String(chartData.times[index])] || [];
    info.textContent = [...lines, ...fills].join("\n");
  }
  showBar();
  chart.subscribeCrosshairMove(param => showBar(indices.get(param.time)));

  function zoom(factor) {
    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;
    const center = (range.from + range.to) / 2;
    const halfWidth = (range.to - range.from) / (2 * factor);
    timeScale.setVisibleLogicalRange({
      from: center - halfWidth,
      to: center + halfWidth,
    });
  }
  document.getElementById("zoom-in").addEventListener("click", () => zoom(1.5));
  document.getElementById("zoom-out").addEventListener("click", () => zoom(1 / 1.5));
  document.getElementById("zoom-fit").addEventListener("click", () => chart.timeScale().fitContent());
  const chartPanel = document.getElementById("chart-panel");
  document.getElementById("fullscreen").addEventListener("click", async () => {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await chartPanel.requestFullscreen();
    }
  });
  document.addEventListener("fullscreenchange", () => {
    document.getElementById("fullscreen").textContent = document.fullscreenElement ? "Exit fullscreen" : "Fullscreen";
  });

  chart.timeScale().fitContent();
  applyTheme();
})();
