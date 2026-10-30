"""Emit 30-minute NIFTY spot OHLC from full one-minute OHLC bars."""

from _spot_ohlc import main


if __name__ == "__main__":
    main(close_only=False)
