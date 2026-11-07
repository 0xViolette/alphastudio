"""Market-data scenarios and end-to-end analysis orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from .backtest import BacktestError, Bar, ROOT, run_backtest
from .costs import CostConfig, synthetic_schema
from .ledger import AccountSeries, replay, sign
from .metrics import Performance, analyze
from .synthetic import (
    EXTREMA_EMITTER,
    FUTURES_CSV,
    FUTURES_EMITTER,
    NIFTY_CSV,
    NIFTY_MONTH2_CSV,
    OPTIONS_EMITTER,
    OptionCostModel,
    build_expiry_calendar,
    buy_and_hold,
    load_option_net,
    make_chart_bars,
    make_option_lookup,
    option_trading_dates,
    synthetic_bars_with_rollover,
    synthetic_close_series,
)


@dataclass(frozen=True, slots=True)
class Scenario:
    kind: str
    emitter: Path
    data_path: Path
    data_label: str
    signals: str = "native"
    ohlc: str = "native"

    @property
    def slug(self):
        if self.kind == "spot":
            return f"spot-{self.ohlc}"
        if self.kind == "synthetic":
            suffix = "-extrema" if self.ohlc == "extrema" else ""
            return f"synthetic-{self.signals}{suffix}"
        return self.kind


@dataclass(slots=True)
class ScenarioResult:
    scenario: Scenario
    strategy: str
    bars: list[Bar]
    chart_bars: list[Bar]
    account: AccountSeries
    performance: Performance
    trade_log: list[dict]


def make_scenario(kind: str, *, signals="options", ohlc=None) -> Scenario:
    if kind == "futures":
        return Scenario(
            kind="futures",
            emitter=FUTURES_EMITTER,
            data_path=FUTURES_CSV,
            data_label="new_data.csv",
        )
    if kind == "spot":
        ohlc = ohlc or "full"
        if ohlc not in ("full", "close-only"):
            raise ValueError("spot OHLC must be 'full' or 'close-only'")
        filename = (
            "spot_full_ohlc_emitter.py"
            if ohlc == "full"
            else "spot_close_only_emitter.py"
        )
        return Scenario(
            kind="spot",
            emitter=ROOT / "research" / "emitters" / filename,
            data_path=ROOT / "data" / "raw" / "nifty50_1min.csv",
            data_label=f"NIFTY spot 30-minute {ohlc} OHLC",
            ohlc=ohlc,
        )
    if kind == "synthetic":
        if signals not in ("futures", "options"):
            raise ValueError("synthetic signals must be 'futures' or 'options'")
        ohlc = ohlc or ("close" if signals == "options" else "native")
        if signals == "futures" and ohlc != "native":
            raise ValueError("--ohlc extrema/close requires --signals options")
        if signals == "options" and ohlc not in ("close", "extrema"):
            raise ValueError("option synthetic OHLC must be 'close' or 'extrema'")
        emitter = FUTURES_EMITTER
        if signals == "options":
            emitter = EXTREMA_EMITTER if ohlc == "extrema" else OPTIONS_EMITTER
        return Scenario(
            kind="synthetic",
            emitter=emitter,
            data_path=FUTURES_CSV if signals == "futures" else NIFTY_CSV,
            data_label=(
                "synthetic futures from option legs + real futures signals"
                if signals == "futures"
                else f"option-derived synthetic futures ({ohlc})"
            ),
            signals=signals,
            ohlc=ohlc,
        )
    raise ValueError(f"unknown scenario {kind!r}")


def run_scenario(
    scenario: Scenario,
    *,
    strategy: str,
    config: CostConfig,
    rf=0.0,
    mar=0.0,
    trials=0,
    initial_source="cost configuration",
) -> ScenarioResult:
    if scenario.kind == "synthetic":
        return _run_synthetic(
            scenario,
            strategy=strategy,
            config=config,
            rf=rf,
            mar=mar,
            trials=trials,
            initial_source=initial_source,
        )
    return _run_standard(
        scenario,
        strategy=strategy,
        config=config,
        rf=rf,
        mar=mar,
        trials=trials,
        initial_source=initial_source,
    )


def _run_standard(
    scenario,
    *,
    strategy,
    config,
    rf,
    mar,
    trials,
    initial_source,
):
    result = run_backtest(strategy, config.argv(), emitter=scenario.emitter)
    if not any(bar.fills for bar in result.bars):
        raise BacktestError("no fills found in strategy output")
    bars = result.bars
    account = replay(bars, config.initial_balance)
    benchmark = [
        config.initial_balance * bar.close / bars[0].close for bar in bars
    ]
    performance = analyze(
        bars,
        account=account,
        initial=config.initial_balance,
        strategy=strategy,
        data=scenario.data_label,
        cost_model=config.label(),
        rf=rf,
        mar=mar,
        trials=trials,
        initial_source=initial_source,
        benchmark_equity=benchmark,
    )
    return ScenarioResult(
        scenario=scenario,
        strategy=strategy,
        bars=bars,
        chart_bars=bars,
        account=account,
        performance=performance,
        trade_log=_standard_trade_log(bars),
    )


def _run_synthetic(
    scenario,
    *,
    strategy,
    config,
    rf,
    mar,
    trials,
    initial_source,
):
    front = load_option_net(NIFTY_CSV)
    month2 = load_option_net(NIFTY_MONTH2_CSV)
    if not front[0] or not month2[0]:
        raise BacktestError("both front-month and month2 option data are required")

    front_lookup = make_option_lookup(*front)
    next_month_lookup = make_option_lookup(*month2)
    start, end = _synthetic_window(
        config, front[0], month2[0], cap_futures=scenario.signals == "futures"
    )
    config = replace(
        config,
        from_date=start.isoformat(),
        till_date=end.isoformat(),
    )
    engine_args = config.argv()
    engine_args[engine_args.index("--costs") + 1] = "none"
    source = run_backtest(strategy, engine_args, emitter=scenario.emitter)
    if not any(bar.fills for bar in source.bars):
        raise BacktestError("no fills found in strategy output")

    expiries = build_expiry_calendar(
        option_trading_dates(front[0], month2[0]),
        front[0][0].date(),
        max(front[0][-1].date(), month2[0][-1].date()),
    )
    cost_model = (
        None
        if config.preset == "none"
        else OptionCostModel(synthetic_schema(), config.values)
    )
    one_minute = (
        synthetic_close_series(front[0], front[1], front[2])
        if scenario.signals == "options"
        else None
    )
    bars, reference_closes, roll_events, trade_log = synthetic_bars_with_rollover(
        source.bars,
        front_lookup,
        front[0][0],
        front[0][-1],
        expiries,
        cost_model,
        config.lot_size,
        next_month_lookup,
        one_minute,
    )
    if not bars:
        raise BacktestError("no executable bars within option coverage")

    account = replay(bars, config.initial_balance)
    benchmark = buy_and_hold(bars, reference_closes, config.initial_balance, roll_events)
    performance = analyze(
        bars,
        account=account,
        initial=config.initial_balance,
        strategy=strategy,
        data=scenario.data_label,
        cost_model=(
            "option-derived synthetic futures + option costs + expiry rollover"
            if config.preset != "none"
            else "option-derived synthetic futures + no execution costs"
        ),
        rf=rf,
        mar=mar,
        trials=trials,
        initial_source=initial_source,
        benchmark_equity=benchmark,
    )
    chart_bars = make_chart_bars(
        source.bars,
        bars,
        roll_events,
        synthetic_close_series(month2[0], month2[1], month2[2]),
    )
    return ScenarioResult(
        scenario=scenario,
        strategy=strategy,
        bars=bars,
        chart_bars=chart_bars,
        account=account,
        performance=performance,
        trade_log=trade_log,
    )


def _synthetic_window(config, front_times, month2_times, *, cap_futures):
    start_available = front_times[0].date()
    end_available = min(front_times[-1].date(), month2_times[-1].date())
    if cap_futures:
        futures_end = csv_date_bounds(FUTURES_CSV)[1]
        if futures_end:
            end_available = min(end_available, futures_end)
    start = date.fromisoformat(config.from_date) if config.from_date else start_available
    end = date.fromisoformat(config.till_date) if config.till_date else end_available
    start = max(start, start_available)
    end = min(end, end_available)
    if start > end:
        raise BacktestError("requested dates have no overlapping option coverage")
    return start, end


def csv_date_bounds(path):
    """Read the first and last DD/MM/YYYY date from a chronological CSV."""
    try:
        with path.open("rb") as file:
            file.readline()
            first = file.readline().split(b",", 1)[0].decode()
            file.seek(0, 2)
            position, tail = file.tell(), b""
            while position > 0 and tail.count(b"\n") < 2:
                step = min(position, 4096)
                position -= step
                file.seek(position)
                tail = file.read(step) + tail
            last = [line for line in tail.split(b"\n") if line.strip()][-1]
            last = last.split(b",", 1)[0].decode()
        parse = lambda value: datetime.strptime(value.strip(), "%d/%m/%Y").date()
        return parse(first), parse(last)
    except (OSError, ValueError, IndexError):
        return None, None


def _standard_trade_log(bars):
    entries = []
    position = 0.0
    for bar in bars:
        for fill in bar.fills:
            delta = fill.side * fill.qty
            closing = position != 0 and sign(position) == -sign(delta)
            reversing = closing and abs(delta) > abs(position)
            parts = [("CLOSE" if closing else "OPEN", fill.qty, fill.fee)]
            if reversing:
                closed_quantity = abs(position)
                closed_fee = fill.fee * closed_quantity / fill.qty
                parts = [
                    ("CLOSE", closed_quantity, closed_fee),
                    ("OPEN", fill.qty - closed_quantity, fill.fee - closed_fee),
                ]
            for action, quantity, fee in parts:
                entries.append({
                    "timestamp": bar.dt,
                    "action": action,
                    "side": "BUY" if fill.side > 0 else "SELL",
                    "qty": quantity,
                    "price": fill.price,
                    "fee": fee,
                    "tag": fill.tag,
                })
            position += delta
    return entries
