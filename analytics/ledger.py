"""FIFO accounting and mark-to-market equity for backtest fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .backtest import Bar


def is_zero(value: float) -> bool:
    return abs(value) < 1e-7


def sign(value: float) -> int:
    return 0 if is_zero(value) else (1 if value > 0 else -1)


@dataclass(frozen=True, slots=True)
class Trade:
    pnl: float
    quantity: float
    direction: str
    entry: datetime
    exit: datetime
    entry_price: float
    exit_price: float
    points: float
    entry_i: int
    exit_i: int
    exit_tag: str

    @property
    def bars(self) -> int:
        return self.exit_i - self.entry_i


@dataclass(slots=True)
class AccountSeries:
    equity: list[float]
    net_qty: list[float]
    realized_pnl: list[float]
    trades: list[Trade]
    fees: float


@dataclass(slots=True)
class _OpenPosition:
    price: float
    quantity: float
    opened: datetime
    bar_index: int
    fee_per_unit: float


def replay(bars: list[Bar], initial: float) -> AccountSeries:
    realized = 0.0
    fees = 0.0
    positions: list[_OpenPosition] = []
    equity = []
    net_quantities = []
    realized_series = []
    trades = []

    for bar_index, bar in enumerate(bars):
        for fill in bar.fills:
            realized -= fill.fee
            fees += fill.fee
            fee_per_unit = fill.fee / fill.qty if fill.qty else 0.0
            remaining = fill.side * fill.qty

            while (
                positions
                and not is_zero(remaining)
                and sign(positions[0].quantity) == -sign(remaining)
            ):
                position = positions.pop(0)
                direction = float(sign(position.quantity))
                closed = min(abs(position.quantity), abs(remaining))
                gross = direction * closed * (fill.price - position.price)
                realized += gross
                trades.append(
                    Trade(
                        pnl=gross
                        - closed * (position.fee_per_unit + fee_per_unit),
                        quantity=closed,
                        direction="long" if direction > 0 else "short",
                        entry=position.opened,
                        exit=bar.dt,
                        entry_price=position.price,
                        exit_price=fill.price,
                        points=direction * (fill.price - position.price),
                        entry_i=position.bar_index,
                        exit_i=bar_index,
                        exit_tag=fill.tag,
                    )
                )

                left = position.quantity - direction * closed
                if not is_zero(left):
                    positions.insert(
                        0,
                        _OpenPosition(
                            position.price,
                            left,
                            position.opened,
                            position.bar_index,
                            position.fee_per_unit,
                        ),
                    )
                remaining += direction * closed

            if not is_zero(remaining):
                positions.append(
                    _OpenPosition(
                        fill.price,
                        remaining,
                        bar.dt,
                        bar_index,
                        fee_per_unit,
                    )
                )

        unrealized = sum(
            position.quantity * (bar.close - position.price)
            for position in positions
        )
        equity.append(initial + realized + unrealized)
        realized_series.append(realized)
        net_quantities.append(sum(position.quantity for position in positions))

    return AccountSeries(equity, net_quantities, realized_series, trades, fees)
