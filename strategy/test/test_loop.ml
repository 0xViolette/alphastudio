open Lib

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
  let module M = Strategy.Make () in
  let d = mk (module M : Strategy_intf.S) in
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

(* ---------- reference recurrences (independent of the DSL) ---------- *)

let sma_series n xs =
  Array.mapi
    (fun i _ ->
       if i < n - 1
       then None
       else (
         let acc = ref 0. in
         for j = i - (n - 1) to i do
           acc := !acc +. xs.(j)
         done;
         Some (!acc /. Float.of_int n)))
    xs
;;

(* ema n s given s as a float option series: seed = first bar where the
   trailing n values of s are all Some; then the standard recurrence.
   Assumes s stays Some once it becomes Some (true for our scenarios). *)
let ema_series n s =
  let alpha = 2. /. Float.of_int (n + 1) in
  let out = Array.make (Array.length s) None in
  let prev = ref None in
  Array.iteri
    (fun i _ ->
       match !prev with
       | None ->
         if i >= n - 1
         then (
           let acc = ref 0. in
           let ok = ref true in
           for j = i - (n - 1) to i do
             match s.(j) with
             | Some v -> acc := !acc +. v
             | None -> ok := false
           done;
           if !ok
           then (
             let seed = !acc /. Float.of_int n in
             out.(i) <- Some seed;
             prev := Some seed))
       | Some p ->
         (match s.(i) with
          | Some v ->
            let e = (v *. alpha) +. (p *. (1. -. alpha)) in
            out.(i) <- Some e;
            prev := Some e
          | None -> prev := None))
    s;
  out
;;

let bars_since_series threshold xs =
  let out = Array.make (Array.length xs) None in
  let cnt = ref None in
  Array.iteri
    (fun i x ->
       if x > threshold
       then (
         out.(i) <- Some 0.;
         cnt := Some 0.)
       else (
         match !cnt with
         | Some c ->
           out.(i) <- Some (c +. 1.);
           cnt := Some (c +. 1.)
         | None -> ()))
    xs;
  out
;;

(* ------------------------------- scenarios ------------------------------- *)

(* sma is built on [Indicator.sum]: exercise it directly, ahead of the ema
   suites that depend on it for their seed. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = sma_series 3 xs in
  run "sma 3 inp" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let probe = Indicator.sma 3 inp in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 1. Plain ema on an input: sma seed while the recursion is pending, then
   the recursion takes over. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6.; 7.; 8. |] in
  let expected = ema_series 3 (Array.map (fun x -> Some x) xs) in
  run "ema 3 inp" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let probe = Indicator.ema 3 inp in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 2. ema of a history-carrying signal: the seed (sma 3 of sma 4) takes
   as long as it takes; [is_pending] keeps selecting it until the recursion
   has a value. *)
let () =
  let xs = [| 2.; 4.; 6.; 8.; 10.; 12.; 14.; 16.; 18.; 20.; 22.; 24. |] in
  let expected = ema_series 3 (sma_series 4 xs) in
  run "ema 3 (sma 4 inp)" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let probe = Indicator.(ema 3 (sma 4 inp)) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 3. ema of an event-driven signal: the seed is pending until two bars
   after the event fires; the recurrence keeps seeding until then. *)
let () =
  let xs = [| 1.; 1.; 9.; 1.; 1.; 2.; 1.; 1.; 1.; 1. |] in
  let expected = ema_series 3 (bars_since_series 5. xs) in
  run "ema 3 (bars_since (inp > 5))" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = Indicator.(ema 3 (bars_since (inp > !5.))) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 4. Nested recurrences: ema of ema. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6.; 7.; 8.; 9.; 10. |] in
  let expected = ema_series 2 (ema_series 3 (Array.map (fun x -> Some x) xs)) in
  run "ema 2 (ema 3 inp) — nested recurrences" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let probe = Indicator.(ema 2 (ema 3 inp)) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 5. Explicit constant bootstrap: the knot is [Pending] on the first bar,
   then carries the previous result. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = [| Some 0.; Some 1.; Some 2.; Some 3.; Some 4.; Some 5. |] in
  run "recurrence bootstrap const 0, body = prev + 1" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun p -> cond (is_pending p) !0. (p + !1.)) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 6. Without a bootstrap, [Pending] propagates through the body and the
   recurrence never produces a value. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = [| None; None; None; None; None; None |] in
  run "unbootstrapped recurrence stays pending" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun p -> p + !1.) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 7. The fallback branch can come from another signal; it is selected only
   while the knot is pending. *)
let () =
  let xs = [| 1.; 2.; 3.; 4. |] in
  let expected = [| Some 1.; Some 2.; Some 3.; Some 4. |] in
  run "recurrence bootstrap from input" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun p -> cond (is_pending p) inp (p + !1.)) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 8. [cond] + [is_pending] provide a pointwise fallback: the delayed input
   while present, otherwise a constant. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = [| Some 5.; Some 5.; Some 1.; Some 2.; Some 3.; Some 4. |] in
  run "fallback to const 5 until delay 2 inp is present" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let delayed = delay 2 inp in
    let probe = cond (is_pending delayed) (const 5.) delayed in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 9. Latch: hold the value of [inp] from the last bar where it exceeded 5. *)
let () =
  let xs = [| 1.; 1.; 9.; 1.; 1.; 8.; 1. |] in
  let expected = [| None; None; Some 9.; Some 9.; Some 9.; Some 8.; Some 8. |] in
  run "latch: cond event value prev" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun prev -> cond (inp > !5.) inp prev) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 10. A recurrence can combine its previous value with the current input. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5.; 6. |] in
  let expected = [| Some 1.; Some 3.; Some 6.; Some 10.; Some 15.; Some 21. |] in
  run "cumulative sum recurrence" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let probe = recurrence (fun prev -> cond (is_pending prev) inp (prev + inp)) in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 11. bar_index is [tick], which counts bars from 0 (the runtime seeds the
   counter at Some 0 on the first bar), so we read it as a float. *)
let () =
  let xs = [| 1.; 2.; 3.; 4.; 5. |] in
  let expected = [| Some 0.; Some 1.; Some 2.; Some 3.; Some 4. |] in
  run "bar_index" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let inp = input () in
    let probe = tick in
    let module R = (val compile ()) in
    { step_in = (fun x -> R.step [ Set (inp, x) ])
    ; read = (fun () -> value_option (R.value probe))
    })
;;

(* 12. A bool stream enters the graph directly: [ginput] + the generic [Set]
   let [cond]'s condition be an external input rather than a computed
   signal. *)
let () =
  let xs = [| 1.; 3.; 2.; 4. |] in
  let expected = [| Some (-9.); Some 13.; Some (-8.); Some 14. |] in
  run "bool input via ginput" xs expected (fun m ->
    let module M = (val m : Strategy_intf.S) in
    let open M in
    let open Ops in
    let inp = input () in
    let flag = ginput () in
    let probe = cond flag (inp + !10.) (inp - !10.) in
    let module R = (val compile ()) in
    { step_in =
        (fun x -> R.step [ Set (inp, x); Set (flag, Stdlib.( x > 2.5 )) ])
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
