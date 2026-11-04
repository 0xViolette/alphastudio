(* OHLC protocol
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
*)
module Ohlc = struct
  let message_size = 40

  let decode bytes : Bar.t =
    let bytes_to_float offset =
      Int64.float_of_bits (Bytes.get_int64_le bytes offset)
    in
    {
      timestamp = Bytes.get_int64_le bytes 0;
      open_ = bytes_to_float 8;
      high = bytes_to_float 16;
      low = bytes_to_float 24;
      close = bytes_to_float 32;
    }
end
