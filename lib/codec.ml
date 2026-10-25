module Ohlc = struct
  let message_size = 40

  type bar =
    { timestamp : int64
    ; open_ : float
    ; high : float
    ; low : float
    ; close : float
    }

  let decode bytes : bar =
    let bytes_to_float offset =
      Int64.float_of_bits (Bytes.get_int64_le bytes offset)
    in
    { timestamp = Bytes.get_int64_le bytes 0
    ; open_ = bytes_to_float 8
    ; high = bytes_to_float 16
    ; low = bytes_to_float 24
    ; close = bytes_to_float 32
    }
  ;;
end
