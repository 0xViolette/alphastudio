# OHLC Protocol 

## Overview

A stream of OHLC candles.

Each candle is represented by a fixed-size 40 byte message.

## Transport

Messages are transmitted as a raw byte stream over stdin/stdout.

## Message Format

- Encoding: binary
- Byte order: little-endian
- Message size: 40 bytes
- Floating-point representation: IEEE 754 binary64 (`float64`)


+--------+------+---------+-----------+
| Offset | Size | Type    | Field     |
+--------+------+---------+-----------+
| 0      | 8    | uint64  | timestamp |
| 8      | 8    | float64 | open      |
| 16     | 8    | float64 | high      |
| 24     | 8    | float64 | low       |
| 32     | 8    | float64 | close     |
+--------+------+---------+-----------+

Messages are concatenated directly in the stream:

[OHLC][OHLC][OHLC][OHLC]...

## Fields.

+-----------+-----------------------------------------------+
| Field     | Type    | Unit             | Description      |
+-----------+---------+------------------+------------------+
| timestamp | uint64  | Unix seconds     | Candle timestamp |
| open      | float64 | Instrument price | Opening price    |
| high      | float64 | Instrument price | Highest price    |
| low       | float64 | Instrument price | Lowest price     |
| close     | float64 | Instrument price | Closing price    |
+-----------+---------+------------------+------------------+

## Ordering

Candles MUST be transmitted in chronological order by timestamp

