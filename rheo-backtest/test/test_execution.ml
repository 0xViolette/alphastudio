open RheoBacktest
open Order

let failures = ref 0
let tol = 1e-9

let check label got want =
  let ok = Float.abs (got -. want) <= tol *. Float.max 1. (Float.abs want) in
  if not ok then (
    incr failures;
    Printf.eprintf "%s: got %.12g, want %.12g\n" label got want)

let check_int label got want =
  if got <> want then (
    incr failures;
    Printf.eprintf "%s: got %d, want %d\n" label got want)

let check_option label got want =
  match (got, want) with
  | None, None -> ()
  | Some got, Some want -> check label got want
  | _ ->
      incr failures;
      Printf.eprintf "%s: option mismatch\n" label

let check_ids label fills want =
  let got = List.map (fun (f : Order.fill) -> f.id) fills in
  if got <> want then (
    incr failures;
    Printf.eprintf "%s: got [%s], want [%s]\n" label (String.concat "; " got)
      (String.concat "; " want))

let bar ?(open_ = 100.) ?(high = open_) ?(low = open_) ?(close = open_) () :
    Bar.t =
  { timestamp = 0L; open_; high; low; close }

let request id action kind = { Order.id; action; kind }
let enter_long id lots kind = request id (Enter_long lots) kind
let enter_short id lots kind = request id (Enter_short lots) kind
let exit_long id kind = request id Exit_long kind

let run_bar broker bar requests =
  ignore (Broker.replace_orders broker requests);
  Broker.execute_bar broker bar

let broker ?(costs = Costs.none) () =
  Broker.create ~lot_size:10. ~initial_balance:1_000. ~costs ()

let test_round_trip () =
  let b = broker () in
  let fills = run_bar b (bar ()) [ enter_long "enter" 2. Market ] in
  check_ids "round-trip entry" fills [ "enter" ];
  check "entry cash" (Broker.cash b) 1_000.;
  check "entry pnl" (Broker.realised_pnl b) 0.;
  check "entry lots" (Broker.net_lots b) 2.;
  check "entry direction" (Broker.net_dir b) 1.;
  check_option "entry price" (Broker.entry_price b) (Some 100.);
  let fills = run_bar b (bar ~open_:110. ()) [ exit_long "exit" Market ] in
  check_ids "round-trip exit" fills [ "exit" ];
  check "exit cash" (Broker.cash b) 1_200.;
  check "exit pnl" (Broker.realised_pnl b) 200.;
  check "flat lots" (Broker.net_lots b) 0.;
  check "flat direction" (Broker.net_dir b) 0.;
  check_option "flat entry price" (Broker.entry_price b) None

let test_reversal () =
  let b = broker () in
  ignore (run_bar b (bar ()) [ enter_long "long" 2. Market ]);
  let fills = run_bar b (bar ~open_:90. ()) [ enter_short "short" 3. Market ] in
  check_ids "reversal fill" fills [ "short" ];
  check "reversal quantity" (List.hd fills).lots 5.;
  check "reversal cash" (Broker.cash b) 800.;
  check "reversal pnl" (Broker.realised_pnl b) (-200.);
  check "reversal lots" (Broker.net_lots b) 3.;
  check "reversal direction" (Broker.net_dir b) (-1.);
  check_option "reversal entry" (Broker.avg_entry_price b) (Some 90.)

let test_short_cash_balance () =
  let b = broker () in
  ignore (run_bar b (bar ()) [ enter_short "short" 1. Market ]);
  check "short entry cash" (Broker.cash b) 1_000.;
  check "short entry pnl" (Broker.realised_pnl b) 0.;
  ignore (run_bar b (bar ~open_:90. ()) [ request "cover" Exit_short Market ]);
  check "short cover cash" (Broker.cash b) 1_100.;
  check "short cover pnl" (Broker.realised_pnl b) 100.

let test_no_pyramiding () =
  let b = broker () in
  ignore (run_bar b (bar ()) [ enter_long "first" 2. Market ]);
  let fills = run_bar b (bar ~open_:110. ()) [ enter_long "again" 5. Market ] in
  check_ids "same-direction fill blocked" fills [];
  check "same-direction lots unchanged" (Broker.net_lots b) 2.;
  check "same-direction cash unchanged" (Broker.cash b) 1_000.

let test_exit_reentry () =
  let b = broker () in
  ignore (run_bar b (bar ()) [ enter_long "first" 2. Market ]);
  let fills =
    run_bar b (bar ())
      [ exit_long "exit" Market; enter_long "reenter" 3. Market ]
  in
  check_ids "exit then reentry" fills [ "exit"; "reenter" ];
  check "reentry lots" (Broker.net_lots b) 3.;
  check "reentry direction" (Broker.net_dir b) 1.;
  check "reentry cash" (Broker.cash b) 1_000.

let test_path_order () =
  let b = broker () in
  let fills =
    run_bar b
      (bar ~open_:100. ~high:110. ~low:90. ~close:105. ())
      [ enter_long "long" 1. (Stop 105.); enter_short "short" 1. (Stop 95.) ]
  in
  check_ids "OHLC path order" fills [ "long"; "short" ];
  check "OHLC final direction" (Broker.net_dir b) (-1.);
  check "OHLC final lots" (Broker.net_lots b) 1.;
  let b = broker () in
  let fills =
    run_bar b
      (bar ~open_:100. ~high:115. ~low:95. ~close:100. ())
      [ enter_short "short" 1. (Stop 98.); enter_long "long" 1. (Stop 110.) ]
  in
  check_ids "OLHC path order" fills [ "short"; "long" ];
  check "OLHC final direction" (Broker.net_dir b) 1.;
  check "OLHC final lots" (Broker.net_lots b) 1.

let test_gap_fill () =
  let b = broker () in
  let fills =
    run_bar b
      (bar ~open_:110. ~high:120. ~low:100. ~close:115. ())
      [ enter_long "gap" 1. (Stop 105.) ]
  in
  check_ids "gap fill" fills [ "gap" ];
  check "gap uses open" (List.hd fills).price 110.

let test_entry_with_protective_exit () =
  let b = broker () in
  let fills =
    run_bar b
      (bar ~open_:100. ~high:100. ~low:90. ~close:90. ())
      [ exit_long "stop" (Stop 95.); enter_long "entry" 1. Market ]
  in
  check_ids "entry activates protective exit" fills [ "entry"; "stop" ];
  check "protective exit flattens" (Broker.net_lots b) 0.;
  check "protective exit cash" (Broker.cash b) 950.

let test_symmetric_gap_priority () =
  let costs =
    Costs.of_params { Costs.default_params with preset = Costs.Simple }
  in
  let fees fills = List.fold_left (fun acc (f : Order.fill) -> acc +. f.fees) 0. fills in
  let long = broker ~costs () in
  ignore (run_bar long (bar ()) [ enter_long "long" 1. Market ]);
  let fills =
    run_bar long
      (bar ~open_:95. ~high:95. ~low:94. ~close:94. ())
      [ exit_long "stop" (Stop 100.); enter_short "reverse" 1. Market ]
  in
  check_ids "long gap price-path priority" fills [ "stop"; "reverse" ];
  check "long gap fees" (fees fills) 40.;
  let short = broker ~costs () in
  ignore (run_bar short (bar ()) [ enter_short "short" 1. Market ]);
  let fills =
    run_bar short
      (bar ~open_:105. ~high:106. ~low:105. ~close:105. ())
      [ request "stop" Exit_short (Stop 100.); enter_long "reverse" 1. Market ]
  in
  check_ids "short gap price-path priority" fills [ "stop"; "reverse" ];
  check "short gap fees" (fees fills) 40.

let test_costs_and_accounting () =
  let costs =
    Costs.of_params { Costs.default_params with preset = Costs.Simple }
  in
  let b = broker ~costs () in
  let fills = run_bar b (bar ()) [ enter_long "costed" 1. Market ] in
  check "market slippage" (List.hd fills).price 100.05;
  check "fill fee" (List.hd fills).fees 20.;
  check "cash includes fee" (Broker.cash b) 980.;
  check "pnl includes fee" (Broker.realised_pnl b) (-20.)

let expect_invalid_request label requests =
  let b = broker () in
  ignore (Broker.replace_orders b [ enter_long "working" 1. Market ]);
  let raised =
    try
      ignore (Broker.replace_orders b requests);
      false
    with Invalid_argument _ -> true
  in
  if not raised then (
    incr failures;
    Printf.eprintf "%s: invalid request was accepted\n" label);
  let fills = Broker.execute_bar b (bar ()) in
  check_ids (label ^ " preserves working orders") fills [ "working" ]

let test_request_validation () =
  expect_invalid_request "duplicate ids"
    [ enter_long "duplicate" 1. Market; enter_short "duplicate" 1. Market ];
  expect_invalid_request "nan target" [ enter_long "bad" nan Market ];
  expect_invalid_request "infinite target"
    [ enter_long "bad" Float.infinity Market ];
  expect_invalid_request "negative target" [ enter_long "bad" (-1.) Market ];
  expect_invalid_request "nan limit" [ enter_long "bad" 1. (Limit nan) ];
  expect_invalid_request "infinite stop"
    [ enter_long "bad" 1. (Stop Float.infinity) ];
  expect_invalid_request "zero limit" [ enter_long "bad" 1. (Limit 0.) ];
  expect_invalid_request "inactive invalid exit" [ exit_long "bad" (Stop nan) ];
  let b = broker () in
  let active = Broker.replace_orders b [ enter_long "zero" 0. Market ] in
  check_int "zero target remains a no-op" (List.length active) 0

let test_fifo_positions () =
  let open Portfolio in
  let long lots price = { Position.lots; price; side = Position.Long } in
  let short lots price = { Position.lots; price; side = Position.Short } in
  let positions, pnl = Positions.add ~lot_size:10. [] (long 2. 100.) in
  check "initial FIFO pnl" pnl 0.;
  let positions, pnl = Positions.add ~lot_size:10. positions (long 1. 110.) in
  check "same-direction FIFO pnl" pnl 0.;
  check_int "same-direction FIFO length" (List.length positions) 2;
  check "same-direction FIFO lots" (Positions.net_lots positions) 3.;
  check_option "oldest entry" (Positions.entry_price positions) (Some 100.);
  check_option "average entry"
    (Positions.avg_entry_price positions)
    (Some (310. /. 3.));
  let positions, pnl = Positions.add ~lot_size:10. positions (short 2. 105.) in
  check "FIFO reduction pnl" pnl 100.;
  check_int "FIFO reduction length" (List.length positions) 1;
  check_option "FIFO remaining entry"
    (Positions.entry_price positions)
    (Some 110.);
  let positions, pnl = Positions.add ~lot_size:10. positions (short 2. 100.) in
  check "FIFO reversal pnl" pnl (-100.);
  check "FIFO reversal lots" (Positions.net_lots positions) 1.;
  check "FIFO reversal direction" (Positions.dir positions) (-1.);
  check_option "FIFO reversal entry"
    (Positions.entry_price positions)
    (Some 100.)

let () =
  test_round_trip ();
  test_reversal ();
  test_short_cash_balance ();
  test_no_pyramiding ();
  test_exit_reentry ();
  test_path_order ();
  test_gap_fill ();
  test_entry_with_protective_exit ();
  test_symmetric_gap_priority ();
  test_costs_and_accounting ();
  test_request_validation ();
  test_fifo_positions ();
  if !failures = 0 then print_endline "All execution checks passed."
  else (
    Printf.eprintf "%d execution check(s) failed.\n" !failures;
    exit 1)
