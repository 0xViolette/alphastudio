open Rheo

(* Each scenario runs a fresh graph against a hand-computed expected array.

   With [signal] abstract in [Dsl.S], a signal cannot cross a
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
  let module M = Dsl.Make () in
  let d = mk (module M : Dsl.S) in
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

(* 1. map applies pointwise to the unwrapped value. *)
let () =
  run
    "Scenario 1: map (fun x -> x *. 10.)"
    pp_raw_float
    pp_float
    [| 1.; 2. |]
    [| Some 10.; Some 20. |]
    (fun m ->
       let module M = (val m : Dsl.S) in
       let open M in
       let inp = input () in
       let probe = map (fun x -> x *. 10.) inp in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 2. map propagates None (here via the warmup of [pre]). *)
let () =
  run
    "Scenario 2: map over pre (None propagates)"
    pp_raw_float
    pp_float
    [| 5.; 7.; 9. |]
    [| None; Some 6.; Some 8. |]
    (fun m ->
       let module M = (val m : Dsl.S) in
       let open M in
       let inp = input () in
       let probe = map (fun x -> x +. 1.) (pre inp) in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 3. Heterogeneous inputs in one step: a float signal and a bool signal
      set together via [Set] — this is what the pair-list version of [step]
      could not express. *)
let () =
  run
    "Scenario 3: mixed-type step (float + bool)"
    (fun (f, b) -> Printf.sprintf "%g/%b" f b)
    pp_float
    [| 1., true; 2., false; 3., true |]
    [| Some 2.; Some 1.; Some 4. |]
    (fun m ->
       let module M = (val m : Dsl.S) in
       let open M in
       let open M.Ops in
       let price = input () in
       let flag = ginput () in
       let probe = cond flag (price + !1.) (price - !1.) in
       let module R = (val compile ()) in
       { step_in =
           (fun (f, b) -> R.step [ Set (price, f); Set (flag, b) ])
       ; read = (fun () -> R.value probe)
       })
;;

(* 4. A user-level custom recurrence. The knot starts [Pending], so the
       body bootstraps it with [cond] + [is_pending], then increments. *)
let () =
  run
    "Scenario 4: custom recurrence"
    pp_raw_float
    pp_float
    [| 100.; 200.; 300.; 400. |]
    [| Some 0.; Some 1.; Some 2.; Some 3. |]
    (fun m ->
       let module M = (val m : Dsl.S) in
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

(* 5. value_when latches the input from the last bar where the condition
       held. *)
let () =
  run
    "Scenario 5: value_when latch"
    pp_raw_float
    pp_float
    [| 1.; 1.; 9.; 1.; 1.; 8.; 1. |]
    [| None; None; Some 9.; Some 9.; Some 9.; Some 8.; Some 8. |]
    (fun m ->
       let module M = (val m : Dsl.S) in
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
