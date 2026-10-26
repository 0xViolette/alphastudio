# AlphaStudio

A systematic trading research, backtesting, and execution workspace.

## Layout

- `rheo/` — Rheo dataflow DSL (OCaml)
- `rheo-backtest/` — backtesting harness and cost model (OCaml)
- `research/strategies/` — strategy executables
- `research/analysis/` — Python analysis and reporting scripts
- `research/emitters/` — OHLC emitters
- `research/ingest/dhan/` — Dhan data ingestion (Python, local-only)
- `data/raw/`, `data/processed/` — market data (local-only)
- `reports/` — generated reports (local-only)

## Bootstrap

```sh
./scripts/bootstrap.sh
```

Creates the local `_opam` switch (OCaml 5.5.2, Dune 3.24.2, ocamlformat 0.29.0).

## Build and test

```sh
dune build
dune runtest
```

## Python

Analysis scripts run with `uv` from `research/analysis/`:

```sh
cd research/analysis
uv run python plot_pnl.py
uv run python tearsheet.py
uv run python viz.py
```

Dhan ingestion:

```sh
cd research/ingest/dhan
uv run python fetch_csv.py
```
