# Analytics cleanup plan

Status: Core cleanup and output standardization complete.

This document is the living plan for the analytics cleanup. Update it as work proceeds: check off completed items, record verified commands and results, and note any deliberate change to the agreed design.

## 1. Goal and boundaries

Turn the Python analysis directory into one coherent **analytics component** while preserving every current market-data scenario and financial meaning.

AlphaStudio remains the monorepo:

```text
Rheo / strategies → Rheo-backtest → Analytics
   OCaml               OCaml           Python
```

Not in scope:

- Changing DSL, broker, strategy, or execution semantics
- Adding QuantStats as a dependency
- Adding Node.js, React, or a JavaScript build system
- Deleting or modifying market data
- Adding live-trading functionality

QuantStats is an architectural reference only: separate return-based analysis, trade-based analysis, and presentation without delegating the project's calculations to it.

## 2. Agreed behavior

- Keep futures, both spot variants, and all current synthetic workflows.
- Use one top-level `analytics/` Python package, with no `src/` and no nested `analytics/analytics/` directory.
- Expose one `analytics` console command with subcommands.
- Remove old analysis entry scripts after their replacements work.
- Support explicit flags and an optional interactive wizard.
- Always run fresh backtests; do not support saved raw backtest streams.
- Preserve current financial definitions unless a reviewed test establishes an intentional change.
- Keep return-based and trade-based statistics clearly distinguished.
- Produce one self-contained HTML report with always-on account-equity and underwater curves; `--chart` controls only the optional candle pane.
- Provide zoom, fit, and fullscreen controls for the chart area.
- Use QuantStats-inspired summary cards and green/red monthly heatmaps for account returns and closed-trade points.
- Use one Lightweight Charts renderer with real HTML, CSS, and JavaScript source assets.
- Use shadcn-inspired design tokens and component conventions in plain CSS; do not add shadcn/ui, React, Tailwind, or frontend build tooling.
- Remove every PNG-producing workflow.
- Keep terminal statistics, HTML reports, and trade-log CSV output.
- Clear generated `reports/` contents during final cleanup; leave `data/` untouched.

## 3. Target package layout

```text
alphastudio/
├── pyproject.toml
├── rheo/
├── rheo-backtest/
├── rheo-ta/
├── research/
│   ├── strategies/
│   ├── emitters/
│   └── ingest/
└── analytics/
    ├── PLAN.md
    ├── __init__.py
    ├── __main__.py
    ├── cli.py
    ├── backtest.py
    ├── costs.py
    ├── diagnostics.py
    ├── ledger.py
    ├── scenarios.py
    ├── synthetic.py
    ├── metrics.py
    ├── report.py
    ├── templates/
    │   └── report.html
    ├── assets/
    │   ├── report.css
    │   ├── report.js
    │   └── lightweight-charts.js
    └── tests/
```

The root `pyproject.toml`:

- Packages the top-level `analytics/` directory.
- Registers the `analytics` console command.
- Owns the analytics dependencies.
- No longer registers the obsolete `alphastudio` placeholder entry point.

The unused `src/alphastudio` placeholder has been removed.

After implementation and `uv sync`, an activated project environment should support this directly from the repository root:

```bash
analytics report futures --strategy chandelier
```

Without activating the environment:

```bash
uv run analytics report futures --strategy chandelier
```

## 4. Command-line interface

Primary commands:

```bash
analytics report futures --strategy chandelier
analytics report spot --strategy chandelier --ohlc full
analytics report spot --strategy chandelier --ohlc close-only
analytics report synthetic --strategy chandelier --signals futures
analytics report synthetic --strategy chandelier --signals options
analytics report synthetic --strategy chandelier --signals options --ohlc extrema
```

Common reporting options:

```text
--from YYYY-MM-DD
--till YYYY-MM-DD
--costs PRESET and existing cost knobs
--lot-size N
--initial-balance N
--rf N
--mar N
--trials N
--output PATH
--trade-log PATH
--chart
```

Behavior:

- No `--output`: print the terminal report only.
- `--output report.html`: also produce a self-contained HTML report.
- `--chart`: include interactive candles in that HTML report.
- `--trade-log`: export the trade CSV; it no longer means raw backtest input.
- Every report runs a fresh backtest.
- No saved-stream input, `--png`, or performance-image `--out`.

Interactive terminals may offer the wizard for omitted choices. Noninteractive runs will use documented defaults or require the necessary options, never unexpectedly prompt.

`bench_speed.py` will become `analytics benchmark`. The signal checker will become a regression test or diagnostic rather than a reporting command.

## 5. Internal responsibilities

### `backtest.py`

Own only:

- Emitter and strategy subprocesses
- Dune invocation
- Process error handling
- Strict parsing of the current OCaml CSV schema
- Typed `Bar`, `Fill`, and `BacktestResult` objects

Delete legacy event-only side inference, manifest-tag fallback, `fill_cost` compatibility, and saved-stream loading. Parsing fresh process output remains necessary.

### `ledger.py`

Preserve and test:

- FIFO position matching
- Direct reversals
- Realized and unrealized P&L
- Mark-to-market equity
- Closed round trips
- Fees and exposure

Actual engine fill prices and fees remain authoritative.

### `scenarios.py`

Define the six supported workflows declaratively:

- Futures
- Spot with full OHLC
- Spot with close-only OHLC
- Synthetic prices with real-futures signals
- Option-derived synthetic signals and prices
- Option-extrema synthetic signals and prices

Each scenario selects its emitter, input files, and transformation. It should not contain reporting or chart-rendering logic.

### `synthetic.py`

Retain only domain-specific behavior:

- Same-strike call/put loading
- Option quote lookup
- Synthetic price mapping
- Option-leg execution costs
- Expiry calendar
- Position rollover
- Synthetic trade-log events

If necessary, split this by subject into option loading and rollover modules. Avoid generic abstraction layers.

### `metrics.py`

Preserve current definitions through characterization tests.

Keep two explicitly named groups:

- **Return-based:** CAGR, Sharpe, Sortino, drawdowns, daily win rate, PSR, MinTRL, DSR, and benchmark comparisons.
- **Trade-based:** closed-trade win rate, expectancy, payoff, profit factor, streaks, and holding periods.

Reports must distinguish labels such as **“Positive sessions”** from **“Winning closed trades.”**

### `report.py`

Create one typed report model containing:

```text
Run metadata
Account summary
Return-based analysis
Trade-based analysis
Benchmark comparison
Cost summary
Optional chart data
```

Terminal and HTML output consume the same model. Neither recalculates financial metrics.

## 6. HTML and chart cleanup

Maintain substantial web code only in real files:

- `templates/report.html`
- `assets/report.css`
- `assets/report.js`
- Vendored Lightweight Charts

Python will:

1. Prepare the report and optional chart data.
2. Safely serialize the data.
3. Read the template and packaged assets.
4. Assemble one self-contained HTML file.

There will be one chart renderer for ordinary and synthetic results. Plotly candlestick rendering and all PNG generation will disappear. The final HTML must work offline without external scripts or styles.

Package-data configuration and tests will ensure templates and assets remain available after installation.

## 7. Cost consolidation

Keep the OCaml schema as the source of defaults and statutory rates.

- Ordinary reports use actual fill fees from OCaml.
- Synthetic reports calculate option-leg costs once in Python.
- The resulting fees flow directly into the ledger, report, and trade log.
- Remove independent fee recalculation from trade-log formatting.

This preserves current behavior while eliminating competing implementations.

## 8. Files and dependencies to retire

After the replacements pass verification:

- Remove `research/analysis` entry scripts and wrapper scripts.
- Remove `viz.py`, `synthetic_viz.py`, and the current `lwc.py`.
- Remove `plot.py`, `plot_pnl.py`, and the current custom HTML-report implementation.
- Remove the old analysis-specific `pyproject.toml`.
- Remove Plotly, Kaleido, and Matplotlib from analytics dependencies when no remaining code uses them.
- Prune root dependencies only after checking repository-wide usage.
- Update existing README instructions to use `analytics`.
- Clear generated contents of `reports/`, preserving repository placeholders; leave `data/` untouched.

Modules containing needed behavior will be migrated and verified before their old versions are deleted.

## 9. Implementation checklist

### Phase 1: Characterize current behavior

- [x] Add deterministic fixtures for current accounting, metrics, costs, and synthetic rollover.
- [x] Record representative pre-cleanup results for later comparison.
- [x] Establish the existing test baseline.

### Phase 2: Establish the package and core models

- [x] Establish the top-level `analytics` package and console entry point.
- [x] Migrate subprocess handling and strict current-schema parsing into `backtest.py`.
- [x] Introduce typed backtest and report data structures.
- [x] Migrate the ledger and verify equity and round-trip equivalence.

### Phase 3: Migrate scenarios and analytics

- [x] Migrate futures reporting.
- [x] Migrate spot full-OHLC reporting.
- [x] Migrate spot close-only reporting.
- [x] Migrate synthetic reporting with real-futures signals.
- [x] Migrate option-derived synthetic reporting.
- [x] Migrate option-extrema synthetic reporting.
- [x] Separate return-based and trade-based metrics without unintended numerical changes.
- [x] Consolidate cost handling around actual fills and one synthetic option-cost implementation.

### Phase 4: Reporting and visualization

- [x] Implement terminal output using the shared report model.
- [x] Implement the self-contained HTML template and packaged assets.
- [x] Add the optional shared Lightweight Charts section.
- [x] Implement trade-log output without duplicate fee calculations.
- [x] Remove all PNG output paths.

### Phase 5: CLI and migration

- [x] Implement flags and noninteractive behavior.
- [x] Implement the optional wizard.
- [x] Implement `analytics benchmark`.
- [x] Move the signal checker into a regression test or diagnostic.
- [x] Update existing documentation to use the new commands.
- [x] Update package and dependency configuration.

### Phase 6: Deletion and final verification

- [x] Delete superseded scripts and legacy compatibility code only after replacements pass.
- [x] Remove unused plotting dependencies after repository-wide usage checks.
- [x] Clear generated `reports/` contents while preserving repository placeholders.
- [x] Run all end-to-end verification.
- [x] Remove unintended temporary or generated files.

## 10. Verification requirements

Before completion:

- [x] `dune build` and `dune runtest` pass.
- [x] Existing and migrated Python `unittest` suites pass.
- [x] Numerical fixtures match the pre-cleanup implementation or have explicitly reviewed differences.
- [x] Every scenario runs through the real `analytics report ...` command.
- [x] Both chartless and chart-enabled HTML reports open offline.
- [x] Reports contain no PNG workflow or external asset dependency.
- [x] Cost totals reconcile with fills and the ledger.
- [x] Synthetic rollover tests cover positions held flat, long, and short through expiry.
- [x] Noninteractive commands never prompt; wizard behavior is checked separately.
- [x] No generated test artifacts or temporary files remain unintentionally.

## 11. Progress log

### Planning

- Completed the codebase review and identified duplicated orchestration, cost, reporting, and visualization code.
- Agreed on the package boundary, command structure, report outputs, compatibility scope, and deletion policy.
- Saved this living plan at `analytics/PLAN.md`.
- No implementation, dependency, generated-report, or market-data changes have been made.

### Implementation and verification

- Built the top-level `analytics` package and registered `analytics` / `python -m analytics` entry points.
- Replaced legacy orchestration with strict current-schema parsing, typed bars/fills, FIFO accounting, scenario orchestration, and shared performance analysis.
- Confirmed the migrated performance metrics match the legacy implementation exactly on the characterization fixture.
- Preserved futures, spot full-OHLC, spot close-only, synthetic real-futures-signal, synthetic option-signal, and synthetic extrema workflows; each was run through the real CLI.
- Exercised ordinary futures costs and synthetic option costs through real runs.
- Added one self-contained HTML report with optional Lightweight Charts. Python supplies JSON only; markup lives in `templates/report.html`, dynamic rendering in `assets/report.js`, and styling in `assets/report.css`.
- Removed HTML fragment generation from Python, all PNG workflows, Plotly/Kaleido usage, and Matplotlib from the resolved project dependencies.
- Migrated the signal diagnostic to `analytics check-signals` and corrected its chandelier-reference warmup calculation. All 65 flat bars across eight checked probes match within 9.98e-11 relative difference.
- Verified both chart-enabled and chartless reports in an offline Chromium browser through the system Playwright installation. Moved permanent fill labels into the chart readout to avoid overcrowding.
- `dune build` and `dune runtest` pass.
- All 38 migrated Python tests pass.
- The built wheel contains the template, CSS, JavaScript, and vendored Lightweight Charts asset.
- Removed the superseded analysis sources and obsolete root Python placeholder. The two pre-existing generated CSV files under `research/analysis/` were deliberately retained.
- Cleared generated report files while preserving `.gitkeep` placeholders; `data/` was untouched.

### Reporting refinements

- Account-equity and underwater curves are now present in every HTML report; `--chart` adds only the candle pane.
- Added zoom-in, zoom-out, fit, and fullscreen controls. Zoom uses the visible logical range so it works for both curve-only and candle views.
- Added TTY terminal colors with `NO_COLOR` support.
- Added QuantStats-inspired summary cards and color-scaled monthly heatmaps for both account returns and closed-trade points.
- Browser automation verified both report modes, eight summary cards, heatmap styling, visible zoom changes, fit, fullscreen entry/exit, and no JavaScript page errors.

### Extended QuantStats-inspired coverage

- Added 126-session rolling Sharpe, Sortino, volatility, and beta calculations and charts.
- Added EOY strategy/benchmark comparisons, including multiplier and whether the strategy won.
- Added a ten-worst-drawdowns table with start, trough, recovery, depth, and duration.
- Added cumulative log-return and volatility-matched return charts.
- Added daily return distribution with a normal-density overlay and a monthly-return histogram.
- Added repeated `--strategy` flags, independent runs under identical settings, a comparison table, expandable per-strategy details, combined trade logs with strategy attribution, and shared equity overlays.
- Cached option CSV loading within the process for multi-strategy synthetic runs.
- Explicitly excluded Monte Carlo analysis and pandas/notebook integration by user decision.
- Verified a real baseline/chandelier comparison in Chromium, including all new tables, rolling charts, distributions, strategy details, equity overlays, zoom, and fullscreen with no JavaScript page errors.
- Verified a real two-strategy synthetic-futures report through the CLI.

### Terminal drawdown detail

- The detailed report now shows maximum drawdown twice: once in account currency and once as a percentage of the prior equity peak. Verified in a real terminal report as `-86,013` and `-2.84%` for the January 2024 chandelier fixture.

### Single-strategy CLI and complete wizard

- Removed the multi-strategy comparison table, expandable alternative reports, comparison equity overlays, combined trade-log attribution, and repeated `--strategy` handling. Reports again run exactly one strategy.
- Bare `uv run analytics` and `analytics report` now launch the report wizard, including scenario selection. Noninteractive bare invocation prints help.
- The wizard covers every omitted report option: scenario, signal source, OHLC variant, strategy, cost preset and applicable knobs, dates, risk-free rate, minimum acceptable return, trials, lot size, initial balance, HTML output, candles, and trade log.
- Benchmark wizard coverage uses the same shared prompts for its options.
- Documented the exact commands replacing `synthetic_tearsheet.py`, `synthetic_tearsheet_options.py`, `synthetic_tearsheet_options_extrema.py`, and `synthetic_viz.py`.

### Wizard simplification

- Removed wizard prompts for annual risk-free rate, minimum acceptable return, strategy variants tried, lot size, and initial balance. Their flags and defaults remain unchanged.
- The wizard now focuses on scenario variants, strategy, costs, dates, HTML output, candles, and trade logs.

## Output standardization — implemented

Agreed output behavior:

- Terminal-only remains the default; runs do not create files unexpectedly.
- Explicit `--output` and `--trade-log` paths remain available.
- A future `--write` option will create both standard artifacts.
- Standard paths are deterministic and repeated runs replace them:
  - `reports/<scenario>/<strategy>_report.html`
  - `reports/<scenario>/<strategy>_trades.csv`
- Scenario slugs include material variants, for example `spot-full`, `spot-close-only`, `synthetic-futures`, `synthetic-options`, and `synthetic-options-extrema`.
- No timestamped run directories and no JSON output.
- Terminal is the concise human view, HTML the complete interactive report, and CSV the tabular trade record.
- Machine fields use stable snake_case names; quantities are underlying units, fees use account currency, returns are decimals internally, drawdowns are negative, and timestamps include the IST offset.
- Ordinary and synthetic trade logs should share a common core schema, with synthetic-specific fields added rather than redefining core fields.

