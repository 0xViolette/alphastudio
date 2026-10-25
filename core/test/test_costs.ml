open Rheo

let failures = ref 0
let tol = 1e-9

let check label got want =
  let ok = Float.abs (got -. want) <= tol *. Float.max 1. (Float.abs want) in
  if not ok then incr failures;
  Printf.printf
    "  %-34s got=%-18.10g want=%-18.10g %s\n"
    label
    got
    want
    (if ok then "OK" else "MISMATCH")
;;

let check_bool label got =
  if not got then incr failures;
  Printf.printf
    "  %-34s got=%-18s %s\n"
    label
    (string_of_bool got)
    (if got then "OK" else "MISMATCH")
;;

let check_str label got want =
  let ok = String.equal got want in
  if not ok then incr failures;
  Printf.printf
    "  %-34s got=%-18s want=%-18s %s\n"
    label
    got
    want
    (if ok then "OK" else "MISMATCH")
;;

let bar : Codec.Ohlc.bar =
  { timestamp = 0L; open_ = 20_000.; high = 20_100.; low = 19_900.; close = 20_000. }
;;

let ctx ?(lots = 8.) ?(lot_size = 65.) kind side =
  { Costs.side; kind; price = 20_000.; lots; lot_size; bar }
;;

let round_trip model ~lot_size ~lots ~price kind side =
  let fill =
    { Costs.side
    ; kind
    ; price
    ; lots
    ; lot_size
    ; bar = { bar with open_ = price; high = price +. 100.; low = price -. 100.; close = price }
    }
  in
  model.Costs.apply fill
;;

let raises f =
  try
    f ();
    false
  with
  | Costs_cli.Error _ | Invalid_argument _ -> true
;;

let () =
  print_endline "== cost models";
  let futures = Costs.of_params Costs.default_params in
  let buy = round_trip futures ~lot_size:65. ~lots:1. ~price:22_800. (Order.Limit 22_800.) Order.Buy in
  let sell = round_trip futures ~lot_size:65. ~lots:1. ~price:22_800. (Order.Limit 22_800.) Order.Sell in
  check "futures buy fee" buy.fee 86.991068;
  check "futures sell fee" sell.fee 798.351068;
  check "futures round-trip fee" (buy.fee +. sell.fee) 885.342136;
  check
    "futures slippage (mkt)"
    (round_trip futures ~lot_size:65. ~lots:1. ~price:22_800. Order.Market Order.Buy).slippage
    2.10;
  check
    "futures limit slippage"
    (round_trip futures ~lot_size:65. ~lots:1. ~price:22_800. (Order.Limit 22_800.) Order.Buy).slippage
    0.;

  let synthetic =
    Costs.of_params { Costs.default_params with preset = Costs.Synthetic_futures }
  in
  let syn_buy = round_trip synthetic ~lot_size:65. ~lots:8. ~price:20_000. (Order.Limit 20_000.) Order.Buy in
  let syn_sell = round_trip synthetic ~lot_size:65. ~lots:8. ~price:20_000. (Order.Limit 20_000.) Order.Sell in
  check "synthetic buy fee" syn_buy.fee 253.8252448;
  check "synthetic sell fee" syn_sell.fee 284.4012448;
  check
    "synthetic slippage (mkt)"
    (round_trip synthetic ~lot_size:65. ~lots:8. ~price:20_000. Order.Market Order.Buy).slippage
    1.90;
  check
    "synthetic limit slippage"
    (round_trip synthetic ~lot_size:65. ~lots:8. ~price:20_000. (Order.Limit 20_000.) Order.Buy).slippage
    0.;

  let none = Costs.of_params { Costs.default_params with preset = Costs.No_costs } in
  check "none fee" (round_trip none ~lot_size:65. ~lots:1. ~price:22_800. Order.Market Order.Buy).fee 0.;
  check
    "none slippage"
    (round_trip none ~lot_size:65. ~lots:1. ~price:22_800. Order.Market Order.Buy).slippage
    0.;

  print_endline "== tick sizes";
  check "tick below 15k" (Costs.tick_size_for_price 14_999.) 0.05;
  check "tick at 15k" (Costs.tick_size_for_price 15_000.) 0.10;
  check "tick below 30k" (Costs.tick_size_for_price 29_999.) 0.10;
  check "tick at 30k" (Costs.tick_size_for_price 30_000.) 0.20;
  check
    "tick override"
    (Costs.tick_size
       { Costs.default_params with tick_size_override = Some 0.25 }
       (ctx (Order.Limit 20_000.) Order.Buy))
    0.25;

  print_endline "== presets and validation";
  check_str "alias synthetic" (Costs.preset_name (Option.get (Costs.preset_of_string "synthetic"))) "synthetic_futures";
  check_str "alias india" (Costs.preset_name (Option.get (Costs.preset_of_string "india"))) "futures";
  check_bool "negative slip rejected" (raises (fun () ->
    ignore (Costs.of_params { Costs.default_params with slip_ticks = -1. })));
  check_bool "zero tick rejected" (raises (fun () ->
    ignore (Costs.of_params { Costs.default_params with opt_tick_size = 0. })));

  print_endline "== CLI";
  let cfg = Costs_cli.parse_raw [| "strategy"; "--costs"; "none" |] in
  check_str "cli none" cfg.costs.name "none";
  let cfg =
    Costs_cli.parse_raw
      [| "strategy"; "--costs"; "synthetic"; "--call-premium-pct"; "2.5"; "--lot-size"; "70" |]
  in
  check_str "cli synthetic" cfg.costs.name "synthetic_futures";
  check "cli call premium" cfg.params.call_premium_pct 2.5;
  check "cli lot size" cfg.lot_size 70.;
  check_bool "cli invalid slip" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--slip-ticks"; "-1." |])));
  check_bool "cli unknown preset" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--costs"; "bogus" |])));
  let cfg =
    Costs_cli.parse_raw
      [| "strategy"; "--costs"; "none"; "--from"; "2024-01-01"; "--till"; "2024-12-31" |]
  in
  check_bool "cli from" (cfg.from_ts = Some 1704047400L);
  check_bool "cli till is exclusive" (cfg.till_ts = Some 1735669800L);
  check_bool "cli leap day" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--from"; "2023-02-29" |])));
  check_bool "cli feb 30 rejected" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--from"; "2024-02-30" |])));
  check_bool "cli bad month" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--from"; "2024-13-01" |])));
  check_bool "cli bad format" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--from"; "01-01-2024" |])));
  check_bool "cli from after till" (raises (fun () ->
    ignore (Costs_cli.parse_raw [| "strategy"; "--from"; "2025-01-01"; "--till"; "2024-01-01" |])));
  let schema = Costs_cli.schema_json () in
  let contains needle =
    let n = String.length needle in
    let rec loop i =
      i + n <= String.length schema
      && (String.sub schema i n = needle || loop (i + 1))
    in
    loop 0
  in
  check_bool "schema as_of" (contains "\"as_of\":\"2026-10-22\"");
  check_bool "schema defaults" (contains "\"slip_ticks\":1");
  check_bool "schema knobs" (contains "\"call_premium_pct\"");

  print_newline ();
  if !failures = 0
  then print_endline "All cost checks passed."
  else (
    Printf.eprintf "%d cost mismatch(es) found.\n" !failures;
    exit 1)
;;
