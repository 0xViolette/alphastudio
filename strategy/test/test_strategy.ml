open Lib

(* Harness for [Indicator.bars_since] (plus highest/lowest and map checks).
   Each scenario runs a fresh graph against a hand-computed expected array.
   [bars_since c] should be: [None] before the first bar where [c] holds,
   [Some 0.] on any bar where [c] holds, and count up 1, 2, 3... after it.

   With [signal] abstract in [Strategy_intf.S], a signal cannot cross a
   first-class-module boundary, so each scenario builds a concrete [driver]
   record inside the module instead. *)

let pp_float = function
  | None -> "None"
  | Some x -> Printf.sprintf "%g" x
;;

let failures = ref 0

type ('a, 'b) driver =
  { step_in : 'a -> unit
  ; read : unit -> 'b option
  }

let run name pp_in pp_out inputs expected mk =
  let module M = Strategy.Make () in
  let d = mk (module M : Strategy_intf.S) in
  Printf.printf "%s\n" name;
  Printf.printf
    "%-4s %-8s %-8s %-8s %s\n"
    "bar"
    "input"
    "got"
    "want"
    "ok";
  Array.iteri
    (fun i x ->
       d.step_in x;
       let got = d.read () in
       let want = expected.(i) in
       let ok = got = want in
       if not ok then incr failures;
       Printf.printf
         "%-4d %-8s %-8s %-8s %s\n"
         (i + 1)
         (pp_in x)
         (pp_out got)
         (pp_out want)
         (if ok then "OK" else "MISMATCH"))
    inputs;
  print_newline ()
;;

let pp_raw_float = Printf.sprintf "%g"

let bars_since_gt_5 m =
  let module M = (val m : Strategy_intf.S) in
  let open M in
  let open M.Ops in
  let inp = input () in
  let probe = Indicator.bars_since (inp > !5.) in
  let module R = (val compile ()) in
  { step_in = (fun x -> R.step [ Set (inp, x) ])
  ; read = (fun () -> R.value probe)
  }
;;

(* 1. Two events with counting in between. *)
let () =
  run
    "Scenario 1: two events, counting between them"
    pp_raw_float
    pp_float
    [| 1.; 1.; 1.; 9.; 1.; 1.; 9.; 1.; 1.; 1. |]
    [| None
     ; None
     ; None
     ; Some 0.
     ; Some 1.
     ; Some 2.
     ; Some 0.
     ; Some 1.
     ; Some 2.
     ; Some 3.
    |]
    bars_since_gt_5
;;

(* 2. Event on the very first bar: it should register as 0 on bar 1
      and keep counting from there. *)
let () =
  run
    "Scenario 2: event on the first bar"
    pp_raw_float
    pp_float
    [| 9.; 1.; 1.; 1. |]
    [| Some 0.; Some 1.; Some 2.; Some 3. |]
    bars_since_gt_5
;;

(* 3. Back-to-back events: each one resets the count to 0. *)
let () =
  run
    "Scenario 3: consecutive events reset"
    pp_raw_float
    pp_float
    [| 1.; 9.; 9.; 1. |]
    [| None; Some 0.; Some 0.; Some 1. |]
    bars_since_gt_5
;;

(* 4. A condition that is itself [None] while warming up (built on [pre]):
      [bars_since] must stay [None] through the warmup, then behave. *)
let () =
  run
    "Scenario 4: condition with warmup None"
    pp_raw_float
    pp_float
    [| 1.; 9.; 1.; 1. |]
    [| None; None; Some 0.; Some 1. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let open M.Ops in
       let inp = input () in
       let probe = Indicator.bars_since (pre inp > !5.) in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 5/6. Sanity: highest / lowest over a 3-bar window (None during warmup). *)
let () =
  run
    "Scenario 5: highest 3"
    pp_raw_float
    pp_float
    [| 3.; 7.; 2.; 8.; 1.; 4. |]
    [| None; None; Some 7.; Some 8.; Some 8.; Some 8. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let inp = input () in
       let probe = Indicator.highest 3 inp in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

let () =
  run
    "Scenario 6: lowest 3"
    pp_raw_float
    pp_float
    [| 3.; 7.; 2.; 8.; 1.; 4. |]
    [| None; None; Some 2.; Some 2.; Some 1.; Some 1. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let inp = input () in
       let probe = Indicator.lowest 3 inp in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 7. map applies pointwise to the unwrapped value. *)
let () =
  run
    "Scenario 7: map (fun x -> x *. 10.)"
    pp_raw_float
    pp_float
    [| 1.; 2. |]
    [| Some 10.; Some 20. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let inp = input () in
       let probe = map (fun x -> x *. 10.) inp in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 8. map propagates None (here via the warmup of [pre]). *)
let () =
  run
    "Scenario 8: map over pre (None propagates)"
    pp_raw_float
    pp_float
    [| 5.; 7.; 9. |]
    [| None; Some 6.; Some 8. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let inp = input () in
       let probe = map (fun x -> x +. 1.) (pre inp) in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 9. Heterogeneous inputs in one step: a float signal and a bool signal
      set together via [Set] — this is what the pair-list version of [step]
      could not express. *)
let () =
  run
    "Scenario 9: mixed-type step (float + bool)"
    (fun (f, b) -> Printf.sprintf "%g/%b" f b)
    pp_float
    [| 1., true; 2., false; 3., true |]
    [| Some 2.; Some 1.; Some 4. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let open M.Ops in
       let price = input () in
       let flag = ginput () in
       let probe = cond flag (price + !1.) (price - !1.) in
       let module R = (val compile ()) in
       { step_in = (fun (f, b) -> R.step [ Set (price, f); Set (flag, b) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 10. A user-level custom recurrence. The knot starts [Pending], so the
       body bootstraps it with [cond] + [is_pending], then increments. *)
let () =
  run
    "Scenario 10: custom recurrence"
    pp_raw_float
    pp_float
    [| 100.; 200.; 300.; 400. |]
    [| Some 0.; Some 1.; Some 2.; Some 3. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let open M.Ops in
       let inp = input () in
       let counter =
         recurrence (fun prev ->
           cond (is_pending prev) (const 0.) (prev + !1.))
       in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value counter)
       })
;;

(* 11. value_when latches the input from the last bar where the condition
       held. *)
let () =
  run
    "Scenario 11: value_when latch"
    pp_raw_float
    pp_float
    [| 1.; 1.; 9.; 1.; 1.; 8.; 1. |]
    [| None; None; Some 9.; Some 9.; Some 9.; Some 8.; Some 8. |]
    (fun m ->
       let module M = (val m : Strategy_intf.S) in
       let open M in
       let open M.Ops in
       let inp = input () in
       let probe = value_when (inp > !5.) inp in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

let () =
  if !failures = 0
  then print_endline "All scenarios passed."
  else (
    Printf.eprintf "%d mismatch(es) found.\n" !failures;
    exit 1)
;;
