(* CLI surface for cost configuration. Parsing is deliberately kept in the
   library so bin/main.ml, tests, and future executables share one contract.
   There is no environment-variable or .env fallback: an explicit flag wins,
   otherwise the built-in defaults are used. *)

type config =
  { costs : Costs.t
  ; params : Costs.params
  ; lot_size : float
  ; initial_balance : float
  ; from_ts : int64 option
  ; till_ts : int64 option (* exclusive: first midnight IST after the till date *)
  }

exception Error of string

let default_lot_size = 65.
let default_initial_balance = 3_000_000.

(* days-from-civil (Howard Hinnant) and back, for strict YYYY-MM-DD parsing
   without a calendar dependency *)
let days_from_civil y m d =
  let y = if m <= 2 then y - 1 else y in
  let era = (if y >= 0 then y else y - 399) / 400 in
  let yoe = y - (era * 400) in
  let doy = ((153 * (if m > 2 then m - 3 else m + 9)) + 2) / 5 + d - 1 in
  let doe = (yoe * 365) + (yoe / 4) - (yoe / 100) + doy in
  (era * 146097) + doe - 719468
;;

let is_leap y = (y mod 4 = 0 && y mod 100 <> 0) || y mod 400 = 0
let month_days = [| 31; 28; 31; 30; 31; 30; 31; 31; 30; 31; 30; 31 |]

let is_digits s =
  let rec loop i =
    i = String.length s || ((s.[i] >= '0' && s.[i] <= '9') && loop (i + 1))
  in
  String.length s > 0 && loop 0
;;

(* midnight IST of a YYYY-MM-DD date as unix seconds; bar timestamps use the
   same fixed +05:30 offset (see the data emitter) *)
let unix_of_date s =
  let bad () = raise (Error (Printf.sprintf "bad date %S (want YYYY-MM-DD)" s)) in
  match String.split_on_char '-' s with
  | [ ys; ms; ds ]
    when String.length ys = 4
         && String.length ms = 2
         && String.length ds = 2
         && is_digits ys
         && is_digits ms
         && is_digits ds ->
    let y = int_of_string ys and m = int_of_string ms and d = int_of_string ds in
    let dim =
      if m < 1 || m > 12
      then 0
      else if m = 2 && is_leap y
      then 29
      else month_days.(m - 1)
    in
    if d < 1 || d > dim then bad ();
    Int64.(sub (mul (of_int (days_from_civil y m d)) 86400L) 19800L)
  | _ -> bad ()
;;

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
;;

let json_string s = "\"" ^ json_escape s ^ "\""
let json_float x = Printf.sprintf "%.10g" x

let schema_json () =
  let p = Costs.default_params in
  let preset_entries =
    List.map
      (fun (preset, desc) ->
         Printf.sprintf
           "{\"name\":%s,\"description\":%s}"
           (json_string (Costs.preset_name preset))
           (json_string desc))
      Costs.presets
  in
  let knob_entries =
    List.map
      (fun (k : Costs.knob) ->
         let applies =
           List.map json_string k.applies_to |> String.concat ","
         in
         Printf.sprintf
           "{\"key\":%s,\"label\":%s,\"unit\":%s,\"default\":%s,\"min\":%s,\
            \"applies_to\":[%s]}"
           (json_string k.key)
           (json_string k.label)
           (json_string k.unit)
           (json_float k.default)
           (json_float k.min)
           applies)
      Costs.knobs
  in
  let rates =
    Printf.sprintf
      "{\"nse_futures_txn_bps\":%s,\"nse_options_txn_bps\":%s,\
       \"sebi_bps\":%s,\"stt_futures_bps\":%s,\"stt_options_bps\":%s,\
       \"stamp_futures_bps\":%s,\"stamp_options_bps\":%s,\"gst\":%s}"
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
     \"defaults\":{\"slip_ticks\":%s,\"slip_range_pct\":%s,\
     \"fee_per_order\":%s,\"call_premium_pct\":%s,\"put_premium_pct\":%s,\
     \"opt_slip_ticks\":%s,\"opt_slip_pct\":%s,\"opt_tick_size\":%s},\n\
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
    (json_float default_lot_size)
    (json_float default_initial_balance)
    (String.concat "," preset_entries)
    (String.concat "," knob_entries)
    rates
;;

let usage =
  "strategy [--costs PRESET] [cost options] [--from YYYY-MM-DD] [--till \
   YYYY-MM-DD]\n\
  \n\
  Presets: none | futures | synthetic_futures | simple\n\
  (aliases: india=futures, synthetic/options=synthetic_futures)\n\
  \n\
  --from/--till restrict the backtest to a date window (IST; --till is \
   inclusive).\n\
  \n\
  Use --schema for the machine-readable defaults and --print-costs to show \
   the effective model."
;;

let parse_raw argv =
  let current = ref 0 in
  let preset_arg = ref None in
  let slip_ticks = ref None in
  let slip_range_pct = ref None in
  let fee_per_order = ref None in
  let call_premium_pct = ref None in
  let put_premium_pct = ref None in
  let opt_slip_ticks = ref None in
  let opt_slip_pct = ref None in
  let tick_size = ref None in
  let opt_tick_size = ref None in
  let lot_size = ref default_lot_size in
  let initial_balance = ref default_initial_balance in
  let from_arg = ref None in
  let till_arg = ref None in
  let print_costs = ref false in
  let set_float r = Arg.Float (fun x -> r := Some x) in
  let specs =
    [ "--costs", Arg.String (fun s -> preset_arg := Some s),
      "PRESET  none | futures | synthetic_futures | simple"
    ; "--slip-ticks", set_float slip_ticks, "F  futures slippage in ticks"
    ; "--slip-range-pct", set_float slip_range_pct,
      "F  futures slippage as % of bar range"
    ; "--fee-per-order", set_float fee_per_order, "F  flat fee per order"
    ; "--call-premium-pct", set_float call_premium_pct,
      "F  synthetic call premium as % of underlying"
    ; "--put-premium-pct", set_float put_premium_pct,
      "F  synthetic put premium as % of underlying"
    ; "--opt-slip-ticks", set_float opt_slip_ticks,
      "F  option slippage in ticks per leg"
    ; "--opt-slip-pct", set_float opt_slip_pct,
      "F  option slippage as % of premium"
    ; "--tick-size", set_float tick_size,
      "F  futures tick override (default: NSE price step)"
    ; "--opt-tick-size", set_float opt_tick_size,
      "F  option tick size (default 0.05)"
    ; "--lot-size", Arg.Float (fun x -> lot_size := x),
      "F  contract lot size (default 65)"
    ; "--initial-balance", Arg.Float (fun x -> initial_balance := x),
      "F  starting account balance (default 3000000)"
    ; "--from", Arg.String (fun s -> from_arg := Some s),
      "YYYY-MM-DD  first day of the backtest (IST)"
    ; "--till", Arg.String (fun s -> till_arg := Some s),
      "YYYY-MM-DD  last day of the backtest, inclusive (IST)"
    ; "--print-costs", Arg.Set print_costs,
      "print the effective cost model to stderr"
    ]
  in
  let anon arg = raise (Error ("unexpected argument: " ^ arg)) in
  Arg.parse_argv ~current argv (Arg.align specs) anon usage;
  let p = Costs.default_params in
  let choose current fallback = Option.value current ~default:fallback in
  let preset =
    match !preset_arg with
    | None -> p.preset
    | Some s ->
      (match Costs.preset_of_string s with
       | Some preset -> preset
       | None ->
         raise
           (Error
              (Printf.sprintf
                 "unknown --costs preset %S (want \
                  none|futures|synthetic_futures|simple)"
                 s)))
  in
  let params : Costs.params =
    { preset
    ; slip_ticks = choose !slip_ticks p.slip_ticks
    ; slip_range_pct = choose !slip_range_pct p.slip_range_pct
    ; fee_per_order = choose !fee_per_order p.fee_per_order
    ; call_premium_pct = choose !call_premium_pct p.call_premium_pct
    ; put_premium_pct = choose !put_premium_pct p.put_premium_pct
    ; opt_slip_ticks = choose !opt_slip_ticks p.opt_slip_ticks
    ; opt_slip_pct = choose !opt_slip_pct p.opt_slip_pct
    ; tick_size_override =
        (match !tick_size with
         | None -> p.tick_size_override
         | Some v -> Some v)
    ; opt_tick_size = choose !opt_tick_size p.opt_tick_size
    }
  in
  if !lot_size <= 0. || not (Float.is_finite !lot_size)
  then raise (Error "--lot-size must be a finite number > 0");
  if !initial_balance <= 0. || not (Float.is_finite !initial_balance)
  then raise (Error "--initial-balance must be a finite number > 0");
  let from_ts = Option.map unix_of_date !from_arg in
  let till_ts =
    (* exclusive bound: first midnight IST after the till date *)
    Option.map (fun s -> Int64.add (unix_of_date s) 86400L) !till_arg
  in
  (match from_ts, till_ts with
   | Some f, Some t when Int64.compare f t >= 0 ->
     raise (Error "--from must not be after --till")
   | _ -> ());
  let costs = Costs.of_params params in
  let explicit =
    !preset_arg <> None
    || !slip_ticks <> None
    || !slip_range_pct <> None
    || !fee_per_order <> None
    || !call_premium_pct <> None
    || !put_premium_pct <> None
    || !opt_slip_ticks <> None
    || !opt_slip_pct <> None
    || !tick_size <> None
    || !opt_tick_size <> None
  in
  if !print_costs
  then
    Printf.eprintf
      "costs: %s [%s]\n%!"
      costs.desc
      (if explicit then "cli" else "default");
  { costs
  ; params
  ; lot_size = !lot_size
  ; initial_balance = !initial_balance
  ; from_ts
  ; till_ts
  }
;;

let wants_schema argv =
  Array.exists (fun a -> a = "--schema" || a = "--costs-schema") argv
;;

let parse argv =
  if wants_schema argv
  then (
    print_string (schema_json ());
    exit 0);
  try parse_raw argv with
  | Error msg ->
    prerr_endline ("costs: " ^ msg);
    exit 2
  | Invalid_argument msg ->
    prerr_endline msg;
    exit 2
  | Arg.Bad msg ->
    prerr_string msg;
    exit 2
  | Arg.Help msg ->
    print_string msg;
    exit 0
;;
