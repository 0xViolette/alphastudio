open RheoBacktest

module D = Rheo.Dsl.Make ()

let broker = Broker.create ~lot_size:1. ~initial_balance:1_000. ()

module Env = Env.Make (D) (struct
  let broker = broker
end)

module Machine = (val D.compile ())

let failures = ref 0

let step direction =
  Machine.step
    [ D.Set (Env.open_, 0.)
    ; D.Set (Env.high, 0.)
    ; D.Set (Env.low, 0.)
    ; D.Set (Env.close, 0.)
    ; D.Set (Env.Position.direction, direction)
    ; D.Set (Env.Position.open_lots, Float.abs direction)
    ; D.Set (Env.Account.initial_balance, 1_000.)
    ; D.Set (Env.Account.cash, 1_000.)
    ; D.Set (Env.Account.realised_pnl, 0.)
    ]
;;

let values () =
  List.map
    Machine.value
    [ Env.Position.is_long
    ; Env.Position.is_short
    ; Env.Position.is_flat
    ; Env.Position.entered_long
    ; Env.Position.entered_short
    ; Env.Position.entered_trade
    ; Env.Position.exited_trade
    ]
;;

let names =
  [ "is_long"
  ; "is_short"
  ; "is_flat"
  ; "entered_long"
  ; "entered_short"
  ; "entered_trade"
  ; "exited_trade"
  ]
;;

let show = function
  | None -> "pending"
  | Some value -> string_of_bool value
;;

let check label direction expected =
  step direction;
  let got = values () in
  let ok = got = expected in
  if not ok then incr failures;
  Printf.printf "%-20s %s\n" label (if ok then "OK" else "MISMATCH");
  if not ok
  then
    List.iter2
      (fun name (got, want) ->
         Printf.printf
           "  %-16s got=%-7s want=%s\n"
           name
           (show got)
           (show want))
      names
      (List.combine got expected)
;;

let () =
  List.iter
    (fun (label, direction, expected) -> check label direction expected)
    [ "initial flat", 0., [ Some false; Some false; Some true; None; None; None; None ]
    ; "flat stays flat", 0., [ Some false; Some false; Some true; Some false; Some false; Some false; Some false ]
    ; "flat to long", 1., [ Some true; Some false; Some false; Some true; Some false; Some true; Some false ]
    ; "stay long", 1., [ Some true; Some false; Some false; Some false; Some false; Some false; Some false ]
    ; "long to short", -1., [ Some false; Some true; Some false; Some false; Some true; Some true; Some false ]
    ; "stay short", -1., [ Some false; Some true; Some false; Some false; Some false; Some false; Some false ]
    ; "short to long", 1., [ Some true; Some false; Some false; Some true; Some false; Some true; Some false ]
    ; "long to flat", 0., [ Some false; Some false; Some true; Some false; Some false; Some false; Some true ]
    ; "flat after exit", 0., [ Some false; Some false; Some true; Some false; Some false; Some false; Some false ]
    ; "flat to short", -1., [ Some false; Some true; Some false; Some false; Some true; Some true; Some false ]
    ; "short to flat", 0., [ Some false; Some false; Some true; Some false; Some false; Some false; Some true ]
    ];
  if !failures = 0
  then print_endline "All position helper checks passed."
  else (
    Printf.eprintf "%d position helper mismatch(es) found.\n" !failures;
    exit 1)
;;
