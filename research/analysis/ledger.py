"""FIFO quantity accounting for parsed or synthetically transformed bars."""

from dataclasses import dataclass
from datetime import datetime


def is_zero(value):
    return abs(value) < 1e-7


def sgn(value):
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
    def bars(self):
        return self.exit_i - self.entry_i


@dataclass(slots=True)
class AccountSeries:
    equity: list[float]
    net_qty: list[float]
    realized_pnl: list[float]
    trades: list[Trade]
    fees: float


def replay(bars, initial):
    """Mark open unit quantities to each close and record FIFO round trips."""
    realized, positions, fees = 0.0, [], 0.0
    equity, net, realized_pnl, trades = [], [], [], []
    for i, bar in enumerate(bars):
        for fill in bar.fills:
            realized -= fill.fee
            fees += fill.fee
            fee_per_unit = fill.fee / fill.qty if fill.qty else 0.0
            remaining = fill.side * fill.qty
            while positions and not is_zero(remaining) and sgn(positions[0][1]) == -sgn(remaining):
                entry_price, position_qty, entry_dt, entry_i, entry_fpu = positions[0]
                rest = positions[1:]
                direction = float(sgn(position_qty))
                closed = min(abs(position_qty), abs(remaining))
                gross = direction * closed * (fill.price - entry_price)
                realized += gross
                trades.append(
                    Trade(
                        pnl=gross - closed * (entry_fpu + fee_per_unit),
                        quantity=closed,
                        direction="long" if direction > 0 else "short",
                        entry=entry_dt,
                        exit=bar.dt,
                        entry_price=entry_price,
                        exit_price=fill.price,
                        points=direction * (fill.price - entry_price),
                        entry_i=entry_i,
                        exit_i=i,
                        exit_tag=fill.tag,
                    )
                )
                left = position_qty - direction * closed
                if not is_zero(left):
                    rest = [(entry_price, left, entry_dt, entry_i, entry_fpu)] + rest
                positions = rest
                remaining += direction * closed
            if not is_zero(remaining):
                positions.append((fill.price, remaining, bar.dt, i, fee_per_unit))
        unrealized = sum(
            qty * (bar.close - price) for price, qty, _, _, _ in positions
        )
        equity.append(initial + realized + unrealized)
        realized_pnl.append(realized)
        net.append(sum(qty for _, qty, _, _, _ in positions))
    return AccountSeries(equity, net, realized_pnl, trades, fees)
