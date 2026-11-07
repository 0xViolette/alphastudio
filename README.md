# AlphaStudio

A systematic trading research and backtesting workspace.

## Layout

- `rheo/` — Rheo dataflow DSL (OCaml)
- `rheo-backtest/` — backtesting harness and cost model (OCaml)
- `research/strategies/` — strategy executables
- `analytics/` — Python accounting, analysis, and self-contained HTML reports
- `research/emitters/` — OHLC emitters
- `research/ingest/dhan/` — Dhan data ingestion (Python, local-only)
- `data/raw/`, `data/processed/` — market data (local-only)
- `reports/` — generated reports (local-only)

## Bootstrap

```sh
./scripts/bootstrap.sh
uv sync
```

The bootstrap creates the local `_opam` switch (OCaml 5.5.2, Dune 3.24.2, ocamlformat 0.29.0). `uv sync` creates the Python environment and installs the `analytics` command.

## Build and test

```sh
dune build
dune runtest
uv run python -m unittest discover -s analytics/tests -t . -v
```

## Analytics

Run commands from the repository root with `uv run`:

```sh
uv run analytics report futures --strategy chandelier
uv run analytics report spot --strategy chandelier --ohlc full
uv run analytics report spot --strategy chandelier --ohlc close-only
uv run analytics report synthetic --strategy chandelier --signals futures
uv run analytics report synthetic --strategy chandelier --signals options
uv run analytics report synthetic --strategy chandelier --signals options --ohlc extrema
```

After `source .venv/bin/activate`, omit `uv run` and use `analytics` directly.

Bare `uv run analytics` launches the report wizard. `analytics report` does the same, while a scenario supplied on the command line remains preselected. The wizard covers scenario variants, strategy, costs, dates, HTML output, candles, and trade logs. Advanced statistical and account settings remain available as command-line flags without adding wizard prompts.

Reports accept cost, date-window, and statistical options. Use `--output PATH` for a self-contained HTML report and `--trade-log PATH` for a trade CSV. Every HTML report includes account-equity and underwater curves with zoom, fit, and fullscreen controls, plus rolling Sharpe/Sortino/volatility/beta, EOY benchmark comparisons, the ten worst drawdowns, log-return and volatility-matched charts, and return distributions. Add `--chart` to include interactive candles. Without `--output`, the report is printed to the terminal with color when supported. Noninteractive reports require `--strategy`.

The former synthetic scripts map to these commands:

- `synthetic_tearsheet.py` → `analytics report synthetic --signals futures`
- `synthetic_tearsheet_options.py` → `analytics report synthetic --signals options`
- `synthetic_tearsheet_options_extrema.py` → `analytics report synthetic --signals options --ohlc extrema`
- `synthetic_viz.py` → add `--chart` to the corresponding synthetic report

Examples:

```sh
analytics report futures --strategy chandelier --from 2024-01-01 --till 2024-12-31
analytics report futures --strategy chandelier --output reports/chandelier.html --chart
analytics report synthetic --strategy chandelier --signals options --costs costs
```

Additional diagnostics:

```sh
analytics benchmark --strategy chandelier
analytics check-signals
```

## Dhan ingestion

```sh
cd research/ingest/dhan
uv run python fetch_csv.py
```
