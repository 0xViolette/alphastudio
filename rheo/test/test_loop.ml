open Rheo

(* Recurrence semantics suite: [recurrence body] starts its knot as
   [Pending], runs [body] from the first bar, and feeds each result back on
   the next bar. Bodies use [is_pending] + [cond] when they need an explicit
   bootstrap. Each scenario runs a fresh graph against a hand-computed or
   reference-recurrence expectation. *)

let failures = ref 0
let tol = 1e-9

let opt_close a b =
  match a, b with
  | None, None -> true
  | Some x, Some y -> Float.abs (x -. y) < tol
  | _ -> false
;;

let pp_float = function
  | None -> "None"
  | Some x -> Printf.sprintf "%.6g" x
;;

let value_option = function
  | None -> None
  | Some x -> Some x
;;

type driver =
  { step_in : float -> unit
  ; read : unit -> float option
  }

let run name inputs expected mk =
  let module M = Dsl.Make () in
  let d = mk (module M : Dsl.S) in
  Printf.printf "== %s\n" name;
  Array.iteri
    (fun i x ->
       d.step_in x;
       let got = d.read () in
       let want = expected.(i) in
       let ok = opt_close got want in
       if not ok then incr failures;
       Printf.printf
         "  bar %-2d in=%-6g got=%-10s want=%-10s %s\n"
         i
         x
         (pp_float got)
         (pp_float want)
         (if ok then "OK" else "MISMATCH"))
    inputs;
  print_newline ()
;;

(* ------------------------------- scenarios ------------------------------- *)

(* 1. Explicit constant bootstrap: the knot is [Pending] on the first bar,
   then carries the previous result. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected =
    [| Some 0.; Some 1.; Some 2.; Some 3.; Some 4.; Some 5. |]
  in
  run
    "recurrence bootstrap const 0, body = prev + 1"
    xs
    expected
    (fun m ->
       let module M = (val m : Dsl.S) in
       let open M in
       let open Ops in
       let inp = input () in
       let probe =
         recurrence (fun p -> cond (is_pending p) !0. (p + !1.))
       in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> value_option (R.value probe))
       })
;;

(* 2. Without a bootstrap, [Pending] propagates through the body and the
   recurrence never produces a value. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = [| None; None; None; None; None; None |] in
  run "unbootstrapped recurrence stays pending" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun p -> p + !1.) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 3. The fallback branch can come from another signal; it is selected only
   while the knot is pending. *)
let () =
  let xs = [| 1.; 2.; 3.; 4. |] in
  let expected = [| Some 1.; Some 2.; Some 3.; Some 4. |] in
  run "recurrence bootstrap from input" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe =
      recurrence (fun p -> cond (is_pending p) inp (p + !1.))
    in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 4. [cond] + [is_pending] provide a pointwise fallback: the delayed input
   while present, otherwise a constant. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected =
    [| Some 5.; Some 5.; Some 1.; Some 2.; Some 3.; Some 4. |]
  in
  run
    "fallback to const 5 until delay 2 inp is present"
    xs
    expected
    (fun m ->
       let module M = (val m : Dsl.S) in
       let open M in
       let inp = input () in
       let delayed = delay 2 inp in
       let probe = cond (is_pending delayed) (const 5.) delayed in
       let module R = (val compile ()) in
       { step_in = (fun x -> R.step [ Set (inp, x) ])
       ; read = (fun () -> value_option (R.value probe))
       })
;;

(* 5. Latch: hold the value of [inp] from the last bar where it exceeded 5. *)
let () =
  let xs = [| 1.; 1.; 9.; 1.; 1.; 8.; 1. |] in
  let expected =
    [| None; None; Some 9.; Some 9.; Some 9.; Some 8.; Some 8. |]
  in
  run "latch: cond event value prev" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun prev -> cond (inp > !5.) inp prev) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 6. A recurrence can combine its previous value with the current input. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected =
    [| Some 1.; Some 3.; Some 6.; Some 10.; Some 15.; Some 21. |]
  in
  run "cumulative sum recurrence" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe =
      recurrence (fun prev -> cond (is_pending prev) inp (prev + inp))
    in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 7. bar_index is [tick], which counts bars from 0 (the runtime seeds the
   counter at Some 0 on the first bar), so we read it as a float. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5. |] in
  let expected = [| Some 0.; Some 1.; Some 2.; Some 3.; Some 4. |] in
  run "bar_index" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let inp = input () in
    let probe = tick in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 8. A bool stream enters the graph directly: [ginput] + the generic [Set]
   let [cond]'s condition be an external input rather than a computed
   signal. *)
let () =
  let xs = [| 1.; 3.; 2.; 4. |] in
  let expected = [| Some (-9.); Some 13.; Some (-8.); Some 14. |] in
  run "bool input via ginput" xs expected (fun m ->
    let module M = (val m : Dsl.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let flag = ginput () in
    let probe = cond flag (inp + !10.) (inp - !10.) in
    let module R = (val compile ()) in
    { step_in =
        (fun x ->
          R.step [ Set (inp, x); Set (flag, Stdlib.(x > 2.5)) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

let () =
  if !failures = 0
  then print_endline "All recurrence scenarios behaved as expected."
  else (
    Printf.eprintf "%d mismatch(es) found.\n" !failures;
    exit 1)
;;
