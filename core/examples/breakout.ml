open Rheo

(* Volatility-shock entry + ATR trailing stop, under the recurrence
   semantics: [recurrence body] starts its knot as [Pending] and runs
   [body] from the first bar, feeding each result back on the next bar.
   There is no warmup bookkeeping — a body that needs a seed selects it
   with [cond (is_pending prev) seed ...] while the knot is pending.

   Strategy:
   - ENTRY: close jumps >5% over the previous close (unknown bar).
   - FILL:  latch the close of the shock bar.
   - STOP:  once in, trail max(prev_stop, close - 2*ATR(6)); exit when
     close falls through the stop.

   Act A (the mistake): an unbootstrapped stop loop. The knot starts
   [Pending] and [max prev trail] never escapes it — [Pending] in,
   [Pending] out, forever. The stop is dead; the exit never fires. (The
   fill survives only because its body selects [close] on the event bar
   without touching [prev] — bodies that use prev strictly have no such
   rescue.)

   Act B (the fix): event-driven and history-dependent seeds belong in
   the BODY, selected while the knot is pending (Pine's nz). [cond] gates
   the strategy on the entry event. No fixed bar arithmetic anywhere —
   the strategy starts whenever the event fires and the ATR happens to be
   ready. *)

let pp_float = function
  | None -> "None"
  | Some x -> Printf.sprintf "%.4g" x
;;

let pp_bool = function
  | None -> "None"
  | Some b -> if b then "true" else "false"
;;

let value_option = function
  | None -> None
  | Some x -> Some x
;;

(* ~fixed:false = Act A (unbootstrapped, dead); ~fixed:true = Act B. *)
let build m ~fixed =
  let module M = (val m : Dsl.S) in
  let open M in
  let open M.Ops in
  let close = input () in
  let shock = close > pre close * !1.05 in
  let tr = abs (close - pre close) in
  let atr6 =
    recurrence (fun p ->
      cond (is_pending p) (Indicator.sma 6 tr) ((tr * !(1. /. 6.)) + (p * !(5. /. 6.))))
  in
  let trail = close - (atr6 * !2.) in
  if fixed
  then (
    (* latch the fill price: on the shock bar take close, else keep prev *)
    let fill = recurrence (fun prev -> cond shock close prev) in
    (* entered = Some true from the first shock on *)
    let entered = Indicator.bars_since shock >= !0. in
    (* trail the stop once in; [cond (is_pending prev) trail prev]
       bootstraps the ratchet from the trail itself until the stop has a
       value *)
    let stop =
      recurrence (fun prev ->
        cond entered (Indicator.max (cond (is_pending prev) trail prev) trail) prev)
    in
    let exit = close < stop in
    let module R = (val compile ()) in
    fun c ->
      R.step [ Set (close, c) ];
      R.value shock, R.value fill, R.value stop, R.value exit)
  else (
    (* the unbootstrapped stop: the knot's initial [Pending] flows through
       [max] and back into the knot — the stop never comes to life *)
    let fill = recurrence (fun prev -> cond shock close prev) in
    let stop = recurrence (fun prev -> Indicator.max prev trail) in
    let exit = close < stop in
    let module R = (val compile ()) in
    fun c ->
      R.step [ Set (close, c) ];
      R.value shock, R.value fill, R.value stop, R.value exit)
;;

let run name closes expected_stop ~fixed =
  let module M = Dsl.Make () in
  let step = build (module M : Dsl.S) ~fixed in
  Printf.printf "%s\n" name;
  Printf.printf
    "%-4s %-6s %-7s %-7s %-9s %-6s %s\n"
    "bar"
    "close"
    "shock?"
    "fill"
    "stop"
    "exit?"
    "note";
  let mismatches = ref 0 in
  Array.iteri
    (fun i c ->
       let shock, fill, stop, exit = step c in
        let note =
          if value_option shock = Some true
          then "ENTRY shock"
          else if value_option exit = Some true
          then "STOP HIT -> exit"
          else ""
        in
       let ok =
         match value_option stop, expected_stop.(i) with
         | None, None -> true
         | Some a, Some e -> Float.abs (a -. e) < 1e-4
         | _ -> false
       in
       if not ok then incr mismatches;
       Printf.printf
         "%-4d %-6g %-7s %-7s %-9s %-6s %s %s\n"
         i
         c
         (pp_bool shock)
         (pp_float fill)
         (pp_float stop)
         (pp_bool exit)
         note
         (if ok then "" else "<-- MISMATCH"))
    closes;
  if !mismatches = 0
  then Printf.printf "  (all bars as expected)\n\n"
  else Printf.printf "  %d MISMATCH(ES)\n\n" !mismatches
;;

(* Early shock: entry on bar 2, four bars before the ATR exists. *)
let early =
  [| 100.; 101.; 107.; 108.; 110.; 112.; 115.; 117.; 116.; 114.; 111.; 108.; 105.; 104. |]
;;

(* Act A — the knot's initial Pending circulates through [max] forever:
   the stop (and the strategy) never comes to life. *)
let () =
  run
    "Act A: unbootstrapped stop recurrence (dead)"
    early
    (Array.make 14 None)
    ~fixed:false
;;

(* Act B — body-side seeding: the stop stays None until the ATR exists
   (honest: no stop before that), then trails and catches the decline. *)
let () =
  run
    "Act B: stop seeded in the body via cond/is_pending — early shock"
    early
    [| None
     ; None
     ; None
     ; None
     ; None
     ; None
     ; Some 110. (* first bar the ATR exists: 115 - 2*2.5 *)
     ; Some 112.1667 (* ratchet: 117 - 2*2.4167 *)
     ; Some 112.1667
     ; Some 112.1667
     ; Some 112.1667 (* close 111 < stop: STOP HIT *)
     ; Some 112.1667
     ; Some 112.1667
     ; Some 112.1667
    |]
    ~fixed:true
;;

(* Act B on late-shock data: same code, entry on bar 9; the ATR is long
   since ready, so the stop starts on the entry bar itself. *)
let () =
  run
    "Act B: same code, late shock (bar 9)"
    [| 100.
     ; 100.
     ; 100.
     ; 100.
     ; 100.
     ; 100.
     ; 100.
     ; 100.
     ; 100.
     ; 112.
     ; 113.
     ; 115.
     ; 118.
     ; 117.
    |]
    [| None
     ; None
     ; None
     ; None
     ; None
     ; None
     ; None
     ; None
     ; None
     ; Some 108. (* 112 - 2*2 *)
     ; Some 109.3333
     ; Some 111.2778
     ; Some 113.8981
     ; Some 113.8981
    |]
    ~fixed:true
;;
