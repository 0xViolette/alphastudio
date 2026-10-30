# AlphaStudio Architecture Direction

This document records design ideas for improving abstraction, generalisability,
and composability across the OCaml and Python codebases. It is a reference for
future work, not a description of current behavior or a commitment to implement
everything listed here.

## North star

AlphaStudio should be a research and execution platform with CLI and GUI
clients, not a collection of strategy scripts with interfaces bolted onto them.

Every executable and Python script should ideally be a thin adapter around a
reusable core. The core should not know whether it is being called from a
terminal, a test, a parameter sweep, or a future GUI.

The guiding principles are:

- pure cores;
- explicit data contracts;
- thin adapters;
- mutation confined to execution state;
- composition through stable interfaces rather than source inspection;
- generalisation driven by observed repetition rather than hypothetical features.

## Long-term conceptual model

Four concepts provide a useful organisation for the platform:

1. **Definitions** describe what can be run: strategies, instruments, cost
   models, and data sources.
2. **Runs** are immutable executions of definitions with explicit parameters.
3. **Datasets** are the results: bars, fills, positions, trades, equity,
   signals, and metrics.
4. **Views** are presentations of datasets: charts, tables, tearsheets, and
   comparisons.

A future GUI should be just another consumer of canonical run data and analysis
transforms. It should not become another place where trading or accounting
logic lives.

## 1. Establish one canonical domain model across OCaml and Python

The most important shared concepts should have clear, stable meanings
everywhere:

- `Bar`
- `OrderRequest`
- `RestingOrder`
- `Fill`
- `PositionSnapshot`
- `AccountSnapshot`
- `Trade`
- `RunDescriptor`
- `RunResult`

OCaml can use richer internal types such as `Buy | Sell`; Python and serialized
formats can use `+1`/`-1`. The boundary performs the conversion.

CSV, binary OHLC, JSON, and future API messages should all be treated as
serializations of these objects, not as the domain model itself.

This prevents analysis code from depending on textual details such as
`"LE filled"` or the position of a CSV column.

Keep these trading concepts distinct:

- order action: enter long, exit long, enter short, exit short;
- execution side: buy or sell;
- position side: long, short, or flat;
- signed quantity: numeric direction used for accounting.

## 2. Make the execution timeline explicit

The backtest has a meaningful sequence:

1. Fill resting orders.
2. Read the resulting account and position.
3. Evaluate strategy signals.
4. Place new requests.
5. Emit what happened.

That sequence should be visible in the architecture rather than hidden across
`Backtest`, `Broker`, `Oms`, and `Env`.

The strategy should evaluate against one immutable snapshot:

- the broker owns mutable execution state;
- the engine takes a snapshot after fills;
- the strategy sees only that snapshot;
- strategy output is collected as intentions;
- the OMS applies those intentions afterward.

The current `D.read` closures over broker state in `Env.Position` weaken this
boundary because graph evaluation can read hidden mutable state. Standard
trading state should eventually be supplied as explicit inputs. `read` can
remain an escape hatch in Rheo.

This makes evaluation order easier to understand and test, especially for
entries, reversals, entry-price stops, and multiple fills on one bar.

## 3. Separate engine execution from stdin, CSV, and reporting

`Backtest.backtest` currently combines several responsibilities:

- compiling the strategy;
- reading bars from stdin;
- running orders and fills;
- constructing report records;
- invoking output callbacks.

Separate these conceptually into:

```text
Engine.run strategy bar_source -> execution results
Feed provides bars
Report serializes results
CLI connects stdin -> engine -> stdout
```

The engine API can be pull-based, callback-based, fold-based, or streaming. The
important boundary is that it accepts bars rather than obtaining them from
process-global stdin.

Benefits without requiring a GUI:

- tests can supply precise bar sequences;
- alternative data sources stop requiring subprocesses;
- parameter sweeps can run in-process;
- CSV becomes one output format rather than the internal API;
- live execution can eventually reuse the same engine boundary.

`Report.t` is already close to a per-bar execution result. Over time, domain
results can live in a neutral module while `Report` becomes only a renderer.

## 4. Turn strategies into parameterized, side-effect-free definitions

Each strategy module currently combines argument parsing, engine construction,
graph definition, output, and execution.

The reusable part should be only:

> Given strategy parameters and a strategy-facing environment, produce orders,
> probes, and metadata.

Everything else should be outside the strategy.

This improves:

- unit testing;
- parameter sweeps;
- strategy comparison;
- reusable sizing and risk components;
- future runtime discovery.

It also eliminates the need for separate near-copy files for risk variants.

Keep these configuration categories distinct:

- **Strategy parameters:** risk, lookbacks, and ATR multiples.
- **Instrument parameters:** lot size, tick size, and currency.
- **Execution parameters:** costs, slippage, and execution assumptions.
- **Account parameters:** initial balance and account currency.
- **Data parameters:** source, range, and snapshot identity.

They change for different reasons and should not evolve into one large
configuration object.

## 5. Preserve strategy, order, and probe metadata at construction time

The fill stream now carries execution identity and direction. The remaining
metadata problem concerns static strategy description.

When an order rule is constructed, the system can retain:

- order ID;
- entry or exit role;
- long or short position group;
- market, limit, or stop behavior;
- associated stop probe;
- optional display label.

Probes can eventually carry more than a name:

- price-scaled or account-scaled;
- suitable chart overlay or separate panel;
- grouping and display precision.

The guiding rule is:

> If the strategy knows something declaratively, preserve it as data rather
> than recovering it later with regexes or naming conventions.

This would remove the need for `viz.py` to recover strategy structure by
regex-parsing OCaml source.

Static metadata describes the strategy. It must never override actual execution
data such as fill direction.

## 6. Separate strategy intent into composable components

A strategy contains several different kinds of decisions:

- when to enter;
- how large the position should be;
- where the initial stop belongs;
- when the stop should trail;
- when to exit for non-risk reasons;
- which order executes the decision;
- which values should be observable.

For example, Chandelier can be understood as:

- an entry model: channel breakout with an EMA regime filter;
- a sizing model: ATR risk budget constrained by notional exposure;
- an initial risk model: ATR stop captured at entry;
- a trailing risk model: chandelier exit;
- an exit model: opposing channel or regime signal;
- an observation model: ATR, moving average, entry ATR, stops, and size probes.

Explicit conceptual boundaries would allow sizing to change without touching
entry logic, or the chandelier stop to be reused in another strategy.

Composition should not simply concatenate order lists. It needs explicit
policies for:

- simultaneous entries;
- exit priority;
- request netting;
- one component reducing another component's position;
- duplicate order IDs;
- same-step reversals.

The goal is composition with explicit conflict policies, not arbitrary mixing
of mutable order state.

## 7. Keep Rheo small, generic, and trading-independent

Rheo's abstraction boundary is signals, history, recurrence, and compilation.
Orders, positions, ATR, costs, and reports belong outside it.

Possible future generalisations, when justified by actual strategies, include:

- generic rather than float-only recurrence;
- generic `value_when` or `sample_on`;
- transition combinators such as `changed`;
- named nodes for better compile diagnostics;
- clearer warmup and dependency errors.

These should not be added merely because they are possible. The
float-specialized runtime is valuable for performance, and a larger AST has a
real maintenance cost.

The best Rheo cleanup is a narrow, well-documented public interface with
derived combinators built on a small core.

## 8. Make Python analysis a library first and scripts second

Analysis scripts can be separated conceptually into:

- **Acquisition:** launching OCaml or reading stored output.
- **Decoding:** producing canonical result objects.
- **Accounting:** fills to positions, trades, cash, and equity.
- **Metrics:** account series to performance statistics.
- **Presentation:** charts, terminal reports, HTML, and CSV.

Accounting and metrics should not launch processes or parse files. Presentation
should not independently replay fills.

`plot_pnl.py` is a concrete example: it shares parsing but still has separate
replay logic. This creates the possibility of its PnL differing from
`ledger.py`, tearsheets, or charts.

Every analysis should agree on one canonical accounting pipeline:

```text
Actual fills + market prices
        ->
Position and cash ledger
        ->
Trades and account series
        ->
Performance metrics
        ->
Charts, tables, and reports
```

Synthetic analysis should feed the same accounting pipeline wherever possible
rather than developing parallel definitions of trades or performance.

## 9. Remove global registries from analysis flows

Values such as these are convenient for scripts but create hidden coupling:

- global strategy lists;
- global tag mappings;
- globally parsed order specifications;
- mutable run configuration;
- implicit current strategy or cost configuration.

A clean analysis call receives its complete context explicitly:

- canonical run result;
- instrument metadata;
- strategy metadata;
- analysis configuration.

The result should not depend on which module was imported first or which script
populated a global variable.

This matters particularly for `viz.py` and `lwc.py`, where order metadata and
presentation are closely connected. A chart context can be constructed once and
passed through, making the same chart code usable for historical, synthetic,
compared, or live runs.

## 10. Unify configuration and cost-model contracts

Costs cross several boundaries: OCaml execution, OCaml CLI, Python CLI, JSON
schema, and analysis labels.

The long-term risk is not merely duplicated formulas; it is different
components believing they used the same model when their parameters differ.

A robust approach would have:

- one versioned cost-parameter schema;
- explicit cost-model identity stored with every run;
- contract tests that Python and OCaml interpret the schema identically;
- one canonical label or description generator where practical.

Code generation is optional. A versioned schema plus compatibility tests
already removes much of the risk.

The same principle applies to strategy parameters and instrument metadata.

## 11. Treat instruments, sessions, and rolls as domain data

Instrument behavior should not be buried in strategies or reporting:

- lot size and tick size belong to the instrument;
- currency belongs to the instrument/account boundary;
- timezone and sessions belong to calendars;
- expiry and rollover belong to contract metadata;
- display timezone belongs to presentation.

Synthetic contracts and rollovers are a good reason to introduce canonical roll
events rather than treating them as chart-specific special cases. Those events
can then drive:

- accounting;
- trade annotation;
- charts;
- benchmark construction;
- data-quality checks.

UTC should remain the internal timestamp convention; IST is a display choice.

Time alignment should be explicit for:

- the value of a signal after a bar closes;
- an order generated from that signal;
- the next bar on which that order can fill;
- the account state after that fill.

Many lookahead errors are really alignment errors.

## 12. Make synthetic research an input transformation, not a separate platform

Synthetic research should fit the ordinary pipeline:

```text
Real market data + synthetic contract definition
        ->
Synthetic bars and contract events
        ->
Ordinary strategy execution
        ->
Ordinary ledger and analysis
```

Rollover handling and option-leg execution may require additional events or
cost models, but the resulting fills and account history should remain
compatible with the same downstream analysis.

This avoids maintaining separate normal and synthetic versions of every report
and chart.

## 13. Introduce structured diagnostics at library boundaries

Libraries currently use a mixture of exceptions, process exits, stderr
messages, and formatted strings.

A more reusable boundary would distinguish:

- invalid strategy definition;
- invalid configuration;
- malformed input data;
- strategy evaluation failure;
- execution rejection;
- end of input;
- analysis incompatibility.

The CLI can turn those into exit codes and human-readable messages. Tests can
inspect them precisely. A future GUI can display them without parsing stderr.

Warnings such as data gaps or suspicious prices should be attachable to a run
without changing its financial results.

## 14. Make runs immutable and reproducible

A run should be a first-class object rather than the CSV printed by one
invocation.

A run definition should capture:

- strategy and version;
- complete parameter set;
- instrument and data range;
- data snapshot or content hash;
- initial balance;
- cost and execution models;
- engine version;
- random seed, if relevant.

A run result should retain:

- orders and fills;
- position and account history;
- signals and probes;
- trades and equity;
- diagnostics and warnings;
- references to large data artifacts.

Once a run finishes, its definition and raw result should not change. New
analyses can be computed from it, but the underlying execution remains stable.

This enables trustworthy comparisons, run history, caching, reproducible
research, deterministic debugging, and live-incident replay.

## 15. Make analyses composable transforms

An analysis should declare:

- what datasets it needs;
- what dataset or metrics it produces;
- whether it supports incremental updates;
- whether its output is cacheable.

For example:

- ledger analysis requires fills, bars, lot size, and initial balance;
- trade analysis requires ledger trades;
- drawdown analysis requires an equity series;
- order-attribution analysis requires trades and their order IDs;
- benchmark comparison requires aligned benchmark and account series.

The platform can then construct the required pipeline automatically and reuse
cached intermediate results.

That is real composability: analyses connect through data contracts rather than
calling one another ad hoc.

## 16. Use tests to protect architecture, not only calculations

Beyond unit tests for indicators and costs, the highest-value tests for
generalisability are:

- deterministic engine scenarios from explicit bar sequences;
- ledger invariants for partial closes and reversals;
- identical accounting across normal and synthetic paths;
- golden strategy results at the fill/equity level;
- serialization round trips;
- schema compatibility tests;
- alignment tests proving orders cannot fill before their signals exist.

Golden tests should compare meaningful results such as fills, positions,
equity, and probes, not incidental CSV formatting.

These tests allow internal abstractions to improve without silently changing
financial behavior.

## Future GUI direction

A future GUI should be a linked workspace, not a collection of unrelated pages.
Selecting a trade could show:

- entry and exit markers on the price chart;
- contributing order IDs;
- ATR and stop values at entry;
- position size and risk budget;
- fees and slippage;
- equity before and after;
- exact run parameters.

Useful generic panels include:

- run catalog;
- price and signal chart;
- equity and drawdown chart;
- position and exposure chart;
- trades, orders, and fills table;
- performance summary;
- parameter inspector;
- diagnostics and data-quality panel.

Panels should link through shared identifiers: run, instrument, timestamp,
order, fill, and trade.

The frontend should own interaction and presentation, not financial definitions.
Strategy and analysis forms should be generated from descriptors rather than
source parsing or bespoke frontend logic.

## Avoid excessive generalisation

General and composable does not mean turning everything into a visual node
editor or making every function a plugin.

A small set of stable extension points is enough:

- strategy components;
- data sources;
- execution and cost models;
- analyses;
- UI panels.

The core concepts—runs, fills, positions, trades, accounts, and time
alignment—should remain stable and strongly defined.

Avoid building prematurely:

- a general portfolio allocator;
- a plugin framework;
- an analysis DAG;
- a universal event bus;
- a visual node language;
- generic state-machine support in Rheo solely for hypothetical strategies;
- microservices for every analysis.

These become appropriate when concrete use cases constrain their design.

## Suggested priority order

1. Separate engine execution from stdin and CSV.
2. Make strategies pure, parameter-driven definitions with a shared runner.
3. Unify the canonical ledger and result objects used by Python analyses.
4. Replace hidden broker reads with explicit snapshots.
5. Publish narrow public interfaces and preserve strategy metadata.
6. Replace module-level analysis registries with explicit contexts.
7. Version instrument, cost-model, and serialization contracts.
8. Add immutable run storage and reproducibility metadata.
9. Introduce composable analysis transforms and caching.
10. Build a read-only GUI only after these foundations are useful on their own.

The connecting principle is:

> Move information across module boundaries as explicit data, and keep mutation
> inside the component that owns it.
