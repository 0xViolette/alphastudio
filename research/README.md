# research (Rheo strategies)

Rheo is a synchronous dataflow DSL for trading strategies, embedded in OCaml,
together with a bar-by-bar backtesting engine built on it.

A strategy is a graph of typed signals. Kaon compiles that graph once into a
small instruction machine, then steps it over a stream of OHLC bars. Time is
explicit (`pre`, `delay`, `window`, `recurrence`), warmup is ordinary optional
values, and strategy code contains no mutable state.

The strategy engine is OCaml; analysis and reporting use Python:

```
../docs/protocol/ohlc.md  binary input format
../rheo/lib/          DSL (Rheo), compiler, broker, OMS, reporting
strategies/           strategy executables
../rheo/examples/     executable DSL examples
../rheo/test/         semantics test suites
../rheo-backtest/     backtesting harness
analysis/             Python tearsheets and candlestick visualization
emitters/             OHLC emitters
```

`_opam/`, `_build/`, `data/`, `scratch/`, and generated `*.csv`,
`*.png`, and `*.txt` files are ignored and are not part of the repository.

## Build and test

Requires OCaml 5.5.2 and dune >= 3.24; run from the repository root:

```sh
../scripts/bootstrap.sh
dune build
dune runtest
```

Strategy executables are built under `_build/default/research/strategies/`:

```sh
dune exec research/strategies/chandelier.exe
```

The examples need no input data:

```sh
dune exec rheo/examples/temp.exe      # crossover semantics
dune exec rheo/examples/breakout.exe  # recurrence bootstrapping
```

## Core modules

| Module | Role |
| --- | --- |
| `../rheo/lib/ast.ml` | GADT signal definitions and the small node AST |
| `lib/registry.ml` | node creation, dependency rank, `Pre` hash-consing |
| `lib/graph.ml` | graph-level constructors (`input`, `pre`, `recurrence`, ...) |
| `lib/dsl_intf.ml` | the public DSL signature |
| `lib/dsl.ml` | combinator implementation, `Ops`, indicators, `compile` |
| `lib/machine.ml` | graph-to-instruction compiler and the stepping runtime |

| `rheo-backtest` package | `Backtest`, `Broker`, `Oms`, `Order`, `Env`, `Feed`, `Codec`, `Report`, `Costs`, `Ta` |

## The DSL

### Signals and time

A signal conceptually carries one value per bar. Internally every value is
optional: `None` means "not available yet". Float signals store pending values
as `nan` in flat arrays, while generic signals store them as `None`.

The snippets below assume the library is opened with `open Rheo`.

```ocaml
module M = Dsl.Make ()       (* one independent graph *)

let close = M.input ()       (* float, set every step *)
let flag  = M.ginput ()      (* generic/boolean input *)
let one   = M.const 1.       (* always present *)
let tag   = M.pure "long"    (* always present, generic *)
```

Combinators:

| Combinator | Meaning |
| --- | --- |
| `input ()` / `ginput ()` | float / generic input, supplied by `step` |
| `const x` / `pure x` | always-present float / generic value |
| `undefined ()` | always-absent generic value |
| `pre s` | value of `s` on the previous bar |
| `delay n s` | value of `s` from `n` bars ago |
| `window n s` | `[s; pre s; ...; pre^(n-1) s]`, newest first |
| `map f s`, `map2 f a b` | lift pure float functions |
| `gmap`, `gmap2` | lift option-aware generic functions |
| `lift f s`, `lift2 f a b`, `f <*> a` | applicative helpers |
| `cond c t e` | select `t` or `e` by `c` |
| `is_pending s` | always-present bool: is `s` absent this bar? |
| `value_when c s` | latch `s` from the last bar where `c` held |
| `recurrence f` | stateful feedback loop (see below) |
| `read f` | read external state, re-evaluated every bar |
| `compile ()` | freeze the graph and return a machine |

There are two runtime kinds. `float signal` nodes live in unboxed `float`
arrays; generic `'a signal` nodes (`bool signal`, tuples, order requests) live
in `Obj.t` arrays holding `'a option`. Arithmetic is only available on float
signals, comparisons lift to `bool signal`, and `gmap`/`ginput`/`pure` cover
everything else. A step accepts a heterogeneous `set list`
(`Set : 'a signal * 'a -> set`), so a float price and a boolean flag can be set
together.

`M.Ops` provides the usual float operators plus comparisons and boolean
operators; constants need the `!` constructor:

```ocaml
let above, falling =
  let open M in
  let open M.Ops in
  let above = close > (pre close * !1.01) in
  above, not above
;;
```

Comparisons and boolean operators propagate pending values: comparing against
a signal that is still warming up yields `None`, not `false`. Arithmetic
likewise propagates `nan`. `cond` only needs its condition to be resolved; the
branch that is not selected may still be pending.

`pre` is hash-consed: `pre s` always returns the same node, so repeated use
does not duplicate history. `window n` expands to `n-1` nested `pre` nodes.

`read` is the escape hatch for state outside the graph. The backtest harness
uses it to expose the broker's entry price:

```ocaml
let entry_price = M.read (fun () -> Broker.entry_price broker)
```

### Recurrence

`recurrence body` creates a feedback knot that is `None` on the first bar. The
body runs on every bar and its result becomes the knot's value on the next
bar. A loop that needs a seed selects it while the knot is pending:

```ocaml
let bar, total =
  let open M in
  let open M.Ops in
  (* bar counter: 0, 1, 2, ... *)
  let bar =
    recurrence (fun prev ->
      cond (is_pending prev) !0. (prev + !1.))
  in
  (* cumulative sum of close *)
  let total =
    recurrence (fun prev ->
      cond (is_pending prev) close (prev + close))
  in
  bar, total
;;
```

Without a bootstrap, pending circulates forever:

```ocaml
let _dead = M.recurrence (fun prev -> M.map (fun x -> x +. 1.) prev)
```

This is intentional: bootstrapping is explicit, so a loop only starts producing
values when its own body says it has a valid seed. `Indicator.ema` and
`Ta.atr` use the same pattern internally. `tick` is a prebuilt counter starting
at 0.

### Indicators

`M.Indicator` provides:

| Indicator | Behaviour |
| --- | --- |
| `sma n s` | rolling mean; pending until `n` samples exist |
| `ema n s` | seeded by `sma n s`, then `alpha = 2/(n+1)` |
| `highest n s`, `lowest n s` | current bar plus `n-1` previous bars |
| `crossover a b` | `pre a < pre b && a > b` (strict, single-bar pulse) |
| `crossunder a b` | `pre a > pre b && a < b` |
| `bars_since c` | `0` on a true bar, then `1, 2, ...`; pending before the first true |
| `min`, `max` | pointwise float minimum / maximum |

`crossover` and `crossunder` never count equality on either bar as a crossing.
They are pending during warmup. See `examples/temp.ml` for the exact truth
table.

`Ta.Make` adds bar-aware indicators; currently:

- `tr` — true range, using `pre close`
- `atr n` — Wilder-smoothed true range, seeded by `sma n tr`

In `Backtest.Make ()`, `Indicator` is `Ta.Make` and therefore includes both the
language indicators and `tr`/`atr`.

### Compilation model

`compile ()` freezes the graph: no node may be created after it, and a
`Dsl.Make ()` instance can be compiled only once. `compile` returns a
first-class machine:

```ocaml
module M = Dsl.Make ()
let close = M.input ()
let fast = M.Indicator.ema 10 close
let slow = M.Indicator.ema 30 close
let golden = M.Indicator.crossover fast slow
module R = (val M.compile ())

let () =
  Array.iteri
    (fun i c ->
       R.step [ M.Set (close, c) ];
       match R.value golden with
       | Some true -> Printf.printf "bar %d: golden cross\n" i
       | _ -> ())
    [| 100.; 101.; 102.; 103. |]
```

The compiler:

- collects every node of the graph and orders it by its dependency rank
  (`Pre` adds one level, `Rec` ranks above its body);
- lays float and generic values out in flat arrays (current and previous
  buffers) and turns each computational node into an instruction;
- wires each `Rec` knot to its loop, so `Knot` reads the loop's previous result;
- initializes constants and pure values in both buffers;
- drops nodes that need no instruction (`Undefined`, `Const`, `Pure`,
  `Input`).

Each `step` validates every setter first (same graph, input node, all inputs
set exactly once), then swaps the current/previous buffers, writes the inputs,
and executes the instruction array. A failed step leaves the runtime untouched.
`value` checks graph ownership and maps `nan` back to `None` for float signals.

Because the runtime is just flat arrays and a fixed instruction list, a step
is array indexing and in-place function application. The tests in `test/`
exercise the semantics; `examples/breakout.ml` walks through a subtle
bootstrapping failure and its fix.

## Writing a strategy

At the DSL level, a strategy is ordinary OCaml code that builds signals before
compiling. The snippet above is a complete driver apart from its data array;
for a real strategy, feed bars from the backtest harness instead.

`Backtest.Make ()` bundles:

- the DSL and its machine,
- bar inputs `open_`, `high`, `low`, `close`,
- `Account.cash`, `Account.initial_balance`, `Account.realised_pnl`,
- `Position.direction` (`1.`, `0.`, `-1.`), `Position.open_lots`,
  `Position.entry_price`, `Position.avg_entry_price`,
- `Position.is_long`, `is_short`, `is_flat`, `entered_long`, `entered_short`,
  `entered_trade` (including direct reversals), and `exited_trade` (transition
  to flat),
- `Indicator` including `Ta`,
- order constructors and `backtest`.

Orders are signal-valued: they are evaluated after a bar closes and become
live for the next bar. If their condition remains true they are recomputed and
re-placed on each bar.

```ocaml
open Rheo
module B = Backtest.Make ()

let () =
  let open B in
  let open B.Ops in
  let fast = Indicator.ema 20 close in
  let slow = Indicator.ema 65 close in
  let go_long = Indicator.crossover fast slow in
  let go_flat = Indicator.crossunder fast slow in
  let orders =
    [ enter_long "LE" !1.0 (Stop close) go_long
    ; exit_long "LX" (Stop close) go_flat
    ]
  in
  Printf.printf "%s\n" (Report.csv_header [ "fast"; "slow" ]);
  backtest
    ~probes:[ "fast", fast; "slow", slow ]
    ~on_bar:(fun r -> Printf.printf "%s\n" (Report.csv_row r))
    orders
;;
```

Order constructors:

```ocaml
enter_long  : string -> float signal -> float signal kind -> bool signal -> request signal
enter_short : string -> float signal -> float signal kind -> bool signal -> request signal
exit_long   : string -> float signal kind -> bool signal -> request signal
exit_short  : string -> float signal kind -> bool signal -> request signal
```

The numeric argument to `enter_*` is a target size in lots. `exit_*` flatten
the current position. A request against the current position is netted into a
single order, so one request can close a long and open a short. Same-direction
adds are blocked, i.e. there is no pyramiding.

Kind constructors are `Market`, `Limit price`, and `Stop price`.

### Execution

The harness runs this loop per bar:

1. fill orders left live by the previous bar;
2. step the signal graph with the new OHLC and post-fill account/position
   state;
3. read the order signals and place new requests for the next bar;
4. emit a report record.

Fill rules:

- market orders execute at the next open;
- stop and limit orders gap-fill at the open when the bar opens through the
  level, otherwise fill intrabar when the level lies within `[low, high]`;
- the intrabar path is inferred as `OHLC` when the open is closer to the high
  and `OLHC` when it is closer to the low, which also sequences multiple fills;
- trigger prices use the raw price; slippage and fees apply to the execution.

Fills go through a pluggable cost hook that can adjust the executed price and
debit fees; limit fills do not slip.

`Report.csv_row` emits:

```
Date,Time,Open,High,Low,Close,<probes...>,event,fill_id,fill_side,fill_price,fill_qty,fill_cost
```

The report renders timestamps in IST (UTC+5:30) as `DD/MM/YY` and
`hh:mm AM/PM`.

## Input protocol

The backtester reads a raw stream on stdin. Each bar is a 40-byte little-endian
record:

| Offset | Size | Type | Field |
| --- | --- | --- | --- |
| 0 | 8 | uint64 | timestamp (Unix seconds) |
| 8 | 8 | float64 | open |
| 16 | 8 | float64 | high |
| 24 | 8 | float64 | low |
| 32 | 8 | float64 | close |

Bars must be in chronological order. The reader refuses to read from a
terminal and treats a partial trailing record as an error. See
`protocol/ohlc.md`.

A local data emitter lives in `emitters/`; that directory's output is ignored. Any producer that
writes the 40-byte format can drive the engine:

```sh
producer | dune exec research/strategies/chandelier.exe > out.csv
```

## Extending the DSL

The AST is intentionally small (`../rheo/lib/ast.ml`): `Undefined`, `Input`, `Const`,
`Pure`, `Pre`, `Knot`, `Rec`, `Map`, `Map2`, `Gmap`, `Gmap2`. Derived
combinators are built from these rather than added to the AST.

To add a new primitive node:

1. add its constructor to `Ast.def`;
2. handle it in `Registry.check_operands` and `Registry.rank_of`;
3. add a machine instruction in `Machine` and a case in `Machine.compile` and
   `Machine.exec`;
4. expose the combinator in `Dsl_intf.S` and implement it in `Dsl.Make`.

Only `Pre` is hash-consed, via `signal.pre_child`; other nodes are fresh each
time.

## Test suites

`dune runtest` runs two executables that compare the live machine against
hand-computed arrays:

- `test/test_strategy.ml` — `bars_since`, `highest`/`lowest`, `map`,
  mixed-type steps, recurrence counters, and `value_when`;
- `test/test_loop.ml` — SMA/EMA seeding, nested recurrences, explicit and
  missing bootstraps, latch semantics, `tick`, and generic inputs.

Both exit non-zero on a mismatch. The examples are also executable
documentation: `examples/temp.ml` covers strict crossover behaviour, and
`examples/breakout.ml` demonstrates why event-driven loops must seed their
knot inside the recurrence body.

## Gotchas

- `nan` is the pending marker for float signals, so a real `nan` input is
  indistinguishable from "not available".
- `cond` requires a resolved condition; if the condition is pending, the result
  is pending regardless of the branches.
- Every input must be supplied exactly once per `step`; partial steps are
  rejected before any state changes.
- `compile` is one-shot and freezes the graph; build every signal before it.
- Signals belong to exactly one `Dsl.Make ()` graph; mixing graphs fails at
  construction and again at `step`/`value`.
- Recurrences that use `prev` must bootstrap themselves; a pending value
  propagates through arithmetic forever.
- Orders live for one bar and are recomputed from their signal; there is no
  persistent GTC order object in the strategy layer.
