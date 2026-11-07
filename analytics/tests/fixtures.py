from datetime import datetime

from analytics.backtest import Bar, Fill


def account_bars():
    def bar(stamp, close, fills=()):
        return Bar(datetime.fromisoformat(stamp), close, tuple(fills))

    return [
        bar("2024-01-01 10:00", 105, [Fill(1, 100, 200, 20, "LE")]),
        bar("2024-01-01 11:00", 110, [Fill(-1, 110, 100, 5, "2NL")]),
        bar("2024-01-02 10:00", 95, [Fill(-1, 95, 200, 10, "SE")]),
        bar("2024-01-02 11:00", 90),
        bar("2024-01-02 12:00", 85, [Fill(1, 85, 100, 5, "2NS")]),
    ]
