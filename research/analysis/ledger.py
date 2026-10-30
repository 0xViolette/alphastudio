"""FIFO lot accounting for parsed or synthetically transformed bars."""

from dataclasses import dataclass
from datetime import datetime


def is_zero(value):
    return abs(value) < 1e-7


def sgn(value):
    return 0 if is_zero(value) else (1 if value > 0 else -1)


@dataclass(frozen=True, slots=True)
class Trade:
    pnl: float
    lots: float
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
    net_lots: list[float]
    realized_pnl: list[float]
    trades: list[Trade]
    fees: float


def replay(bars, lot_size, initial):
    """Mark open lots to each close and record fee-net FIFO round trips."""
    realized, lots, fees = 0.0, [], 0.0
    equity, net, realized_pnl, trades = [], [], [], []
    for i, bar in enumerate(bars):
        for fill in bar.fills:
            realized -= fill.fee
            fees += fill.fee
            fee_per_lot = fill.fee / fill.qty if fill.qty else 0.0
            remaining = fill.side * fill.qty
            while lots and not is_zero(remaining) and sgn(lots[0][1]) == -sgn(remaining):
                entry_price, lot_qty, entry_dt, entry_i, entry_fpl = lots[0]
                rest = lots[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                gross = direction * closed * lot_size * (fill.price - entry_price)
                realized += gross
                trades.append(
                    Trade(
                        pnl=gross - closed * (entry_fpl + fee_per_lot),
                        lots=closed,
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
                left = lot_qty - direction * closed
                if not is_zero(left):
                    rest = [(entry_price, left, entry_dt, entry_i, entry_fpl)] + rest
                lots = rest
                remaining += direction * closed
            if not is_zero(remaining):
                lots.append((fill.price, remaining, bar.dt, i, fee_per_lot))
        unrealized = sum(
            sgn(qty) * abs(qty) * lot_size * (bar.close - price)
            for price, qty, _, _, _ in lots
        )
        equity.append(initial + realized + unrealized)
        realized_pnl.append(realized)
        net.append(sum(qty for _, qty, _, _, _ in lots))
    return AccountSeries(equity, net, realized_pnl, trades, fees)
