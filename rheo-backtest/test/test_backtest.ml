open RheoBacktest

let config =
  Cli.parse_raw
    [|
      "test";
      "--costs";
      "simple";
      "--slip-ticks";
      "0";
      "--fee-per-order";
      "20";
      "--lot-size";
      "7";
      "--initial-balance";
      "1000";
    |]

module B = Backtest.Make ()

let () =
  let open B in
  let open Ops in
  enter_long "enter" !2. Market (close > !100.);
  exit_long "exit" Market (Position.is_long && close < !100.);
  observe close "close";
  observe Position.open_lots "lots"

let bar timestamp open_ high low close : Bar.t =
  { timestamp = Int64.of_int timestamp; open_; high; low; close }

let bars =
  List.to_seq
    [
      bar 0 100. 100. 100. 100.;
      bar 60 100. 101. 99. 101.;
      bar 120 102. 103. 101. 102.;
      bar 180 102. 102. 98. 99.;
      bar 240 98. 99. 97. 98.;
    ]

let failures = ref 0

let check_ids label values want =
  if values <> want then (
    incr failures;
    Printf.eprintf "%s: got [%s], want [%s]\n" label
      (String.concat "; " values)
      (String.concat "; " want))

let check_floats label values want =
  if values <> want then (
    incr failures;
    Printf.eprintf "%s: float list mismatch\n" label)

let check_csv got want =
  if got <> want then (
    incr failures;
    Printf.eprintf "CSV mismatch\n--- got ---\n%s\n--- want ---\n%s\n" got want)

let () =
  let reports = ref [] in
  B.run ~config ~on_bar:(fun report -> reports := report :: !reports) bars;
  let reports = List.rev !reports in
  check_ids "fills by bar"
    (List.map
       (fun (r : Bar_report.t) ->
         r.fills |> List.map (fun (f : Order.fill) -> f.id) |> String.concat ",")
       reports)
    [ ""; ""; "enter"; ""; "exit" ];
  check_ids "placements by bar"
    (List.map
       (fun (r : Bar_report.t) ->
         r.new_orders
         |> List.map (fun (o : Order.request) -> o.id)
         |> String.concat ",")
       reports)
    [ ""; "enter"; ""; "exit"; "" ];
  check_floats "post-fill position inputs"
    (List.map
       (fun (r : Bar_report.t) ->
         match List.assoc_opt "lots" r.observations with
         | Some (Some lots) -> lots
         | _ -> nan)
       reports)
    [ 0.; 0.; 2.; 2.; 0. ];
  check_floats "fill prices"
    (reports
    |> List.concat_map (fun (r : Bar_report.t) -> r.fills)
    |> List.map (fun (f : Order.fill) -> f.price))
    [ 102.; 98. ];
  let csv =
    String.concat "\n"
      (Bar_report.csv_header [ "close"; "lots" ]
      :: List.map (Bar_report.csv_row ~lot_size:config.lot_size) reports)
    ^ "\n"
  in
  check_csv csv
    "Date,Time,Open,High,Low,Close,close,lots,event,fill_id,fill_side,fill_price,fill_qty,fill_fee\n\
     01/01/70,05:30 AM,100,100,100,100,100,0,,,,,,\n\
     01/01/70,05:31 AM,100,101,99,101,101,0,enter placed @ mkt,,,,,\n\
     01/01/70,05:32 AM,102,103,101,102,102,2,enter filled,enter,+1,102,14,20\n\
     01/01/70,05:33 AM,102,102,98,99,99,2,exit placed @ mkt,,,,,\n\
     01/01/70,05:34 AM,98,99,97,98,98,0,exit filled,exit,-1,98,14,20\n";
  let second_run_error =
    try
      B.run ~config Seq.empty;
      None
    with Invalid_argument message -> Some message
  in
  if
    second_run_error
    <> Some "Backtest.run: a Backtest instance can only be run once"
  then (
    incr failures;
    Printf.eprintf "second run did not fail with the expected error\n");
  let emit_csv = Array.exists (String.equal "--emit-csv") Sys.argv in
  if !failures = 0 then
    if emit_csv then print_string csv else print_endline "All backtest checks passed."
  else (
    Printf.eprintf "%d backtest check(s) failed.\n" !failures;
    exit 1)
