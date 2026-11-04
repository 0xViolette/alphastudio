let json_escape s =
  let b = Buffer.create (String.length s + 8) in
  String.iter
    (fun c ->
      match c with
      | '"' -> Buffer.add_string b "\\\""
      | '\\' -> Buffer.add_string b "\\\\"
      | '\n' -> Buffer.add_string b "\\n"
      | '\r' -> Buffer.add_string b "\\r"
      | '\t' -> Buffer.add_string b "\\t"
      | c when Char.code c < 0x20 ->
          Buffer.add_string b (Printf.sprintf "\\u%04x" (Char.code c))
      | c -> Buffer.add_char b c)
    s;
  Buffer.contents b

let json_string s = "\"" ^ json_escape s ^ "\""
let json_float x = Printf.sprintf "%.10g" x

let json () =
  let p = Costs.default_params in
  let preset_entries =
    List.map
      (fun (preset, desc) ->
        Printf.sprintf "{\"name\":%s,\"description\":%s}"
          (json_string (Costs.preset_name preset))
          (json_string desc))
      Costs.presets
  in
  let knob_entries =
    List.map
      (fun (k : Costs.knob) ->
        let applies = List.map json_string k.applies_to |> String.concat "," in
        Printf.sprintf
          "{\"key\":%s,\"label\":%s,\"unit\":%s,\"default\":%s,\"min\":%s,\"applies_to\":[%s]}"
          (json_string k.key) (json_string k.label) (json_string k.unit)
          (json_float k.default) (json_float k.min) applies)
      Costs.knobs
  in
  let rates =
    Printf.sprintf
      "{\"nse_futures_txn_bps\":%s,\"nse_options_txn_bps\":%s,\"sebi_bps\":%s,\"stt_futures_bps\":%s,\"stt_options_bps\":%s,\"stamp_futures_bps\":%s,\"stamp_options_bps\":%s,\"gst\":%s}"
      (json_float Costs.rates.nse_futures_txn_bps)
      (json_float Costs.rates.nse_options_txn_bps)
      (json_float Costs.rates.sebi_bps)
      (json_float Costs.rates.stt_futures_bps)
      (json_float Costs.rates.stt_options_bps)
      (json_float Costs.rates.stamp_futures_bps)
      (json_float Costs.rates.stamp_options_bps)
      (json_float Costs.rates.gst)
  in
  Printf.sprintf
    "{\n\
     \"as_of\":%s,\n\
     \"default_preset\":%s,\n\
     \"defaults\":{\"slip_ticks\":%s,\"slip_range_pct\":%s,\"fee_per_order\":%s,\"call_premium_pct\":%s,\"put_premium_pct\":%s,\"opt_slip_ticks\":%s,\"opt_slip_pct\":%s,\"opt_tick_size\":%s},\n\
     \"lot_size\":%s,\n\
     \"initial_balance\":%s,\n\
     \"presets\":[%s],\n\
     \"knobs\":[%s],\n\
     \"rates\":%s\n\
     }\n"
    (json_string Costs.as_of)
    (json_string (Costs.preset_name p.preset))
    (json_float p.slip_ticks)
    (json_float p.slip_range_pct)
    (json_float p.fee_per_order)
    (json_float p.call_premium_pct)
    (json_float p.put_premium_pct)
    (json_float p.opt_slip_ticks)
    (json_float p.opt_slip_pct)
    (json_float p.opt_tick_size)
    (json_float Config.default_lot_size)
    (json_float Config.default_initial_balance)
    (String.concat "," preset_entries)
    (String.concat "," knob_entries)
    rates
