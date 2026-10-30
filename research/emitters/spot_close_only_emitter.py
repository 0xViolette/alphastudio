"""Emit 30-minute NIFTY spot OHLC derived only from one-minute closes."""

from _spot_ohlc import main


if __name__ == "__main__":
    main(close_only=True)
