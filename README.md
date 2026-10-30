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

The main analysis workflows run with `uv` from `research/analysis/`:

```sh
cd research/analysis
uv run python tearsheet.py [STRATEGY]
uv run python synthetic_tearsheet.py [STRATEGY]
uv run python synthetic_tearsheet_options.py [STRATEGY]
uv run python viz.py [--strategy STRATEGY]
```

The tearsheets accept cost, date-window, HTML-export, and trade-CSV flags;
`viz.py` also accepts a saved backtest stream via `--csv PATH`. Run the Python
regression tests with:

```sh
cd research/analysis
uv run python -m unittest discover -s tests -v
```

Dhan ingestion:

```sh
cd research/ingest/dhan
uv run python fetch_csv.py
```
