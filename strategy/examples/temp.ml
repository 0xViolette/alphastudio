open Lib

(* Demo of [Indicator.crossover] / [Indicator.crossunder].
   -----------------------------------------------------
   Strict crossing only: no equality allowed on either bar.

     crossover a b   =  (pre a < pre b) && (a > b)
     crossunder a b  =  (pre a > pre b) && (a < b)

   So breaking *out of* a touch ([pre a = pre b]) does NOT fire, and
   landing exactly *on* the other line ([a = b]) does NOT fire either.
   Each fires a single-bar pulse: it stays false while [a] remains on
   the new side. While warming up (no previous bar yet) it is [Pending]
   (printed None), not [false]. *)

let pp_float = function
  | None -> "None"
  | Some x -> Printf.sprintf "%.2f" x
;;

let pp_bool = function
  | None -> "None"
  | Some b -> if b then "true" else "false"
;;

let value_option = function
  | None -> None
  | Some x -> Some x
;;

(* Part 1: crossovers in isolation, fed hand-picked (fast, slow) pairs. *)
module Raw = Strategy.Make ()

let raw_fast = Raw.input ()
let raw_slow = Raw.input ()

let raw_over, raw_under =
  let open Raw in
  Indicator.crossover raw_fast raw_slow, Indicator.crossunder raw_fast raw_slow
;;

module RawM = (val Raw.compile ())

let raw_probes = [| 1., 2.; 0., 2.; 3., 2.; 4., 2.; 2., 2.; 3., 2.; 1., 2.; 3., 2. |]

let raw_notes =
  [| "warm-up: no previous bar"
   ; "still strictly below"
   ; "CROSS up: strictly below -> strictly above"
   ; "holds above: does not re-fire"
   ; "lands exactly on slow: not a cross"
   ; "breaks out of a touch: NOT a strict cross"
   ; "CROSS down: strictly above -> strictly below"
   ; "CROSS up again"
  |]
;;

let raw_expected_over =
  [| None
   ; Some false
   ; Some true
   ; Some false
   ; Some false
   ; Some false
   ; Some false
   ; Some true
  |]
;;

let raw_expected_under =
  [| None
   ; Some false
   ; Some false
   ; Some false
   ; Some false
   ; Some false
   ; Some true
   ; Some false
  |]
;;

(* Part 2: the classic use — fast/slow SMA trend switch (golden/death cross).
   An up-down-up ramp (not flats) so the SMAs stay strictly separated
   around each crossing instead of sitting exactly equal. *)
module Trend = Strategy.Make ()

let close = Trend.input ()

let fast, slow, cross_over, cross_under =
  let open Trend in
  let fast = Indicator.sma 3 close in
  let slow = Indicator.sma 5 close in
  let cross_over = Indicator.crossover fast slow in
  let cross_under = Indicator.crossunder fast slow in
  fast, slow, cross_over, cross_under
;;

module TrendM = (val Trend.compile ())

let prices =
  [| 5.; 6.; 7.; 8.; 9.; 10.; 9.; 8.; 7.; 6.; 5.; 4.; 5.; 6.; 7.; 8.; 9.; 10. |]
;;

let () =
  Printf.printf "Part 1: strict crossover / crossunder on (fast, slow) pairs\n";
  Printf.printf
    "%-4s %-6s %-6s %-9s %-10s %s\n"
    "bar"
    "fast"
    "slow"
    "over"
    "under"
    "what happened";
  Array.iteri
    (fun i (f, s) ->
       RawM.step [ Set (raw_fast, f); Set (raw_slow, s) ];
       let over = RawM.value raw_over in
       let under = RawM.value raw_under in
       let mark =
         if
           value_option over = raw_expected_over.(i)
           && value_option under = raw_expected_under.(i)
         then "OK"
         else "MISMATCH"
       in
       Printf.printf
         "%-4d %-6.2f %-6.2f %-9s %-10s [%s] %s\n"
         i
         f
         s
         (pp_bool over)
         (pp_bool under)
         mark
         raw_notes.(i))
    raw_probes;
  Printf.printf "\nPart 2: SMA 3 / SMA 5 golden-cross / death-cross strategy\n";
  Printf.printf
    "%-4s %-6s %-6s %-6s %-9s %-9s\n"
    "bar"
    "close"
    "fast"
    "slow"
    "crossover"
    "crossunder";
  let buys = ref 0 in
  let sells = ref 0 in
  Array.iteri
     (fun i p ->
        TrendM.step [ Set (close, p) ];
        let co = TrendM.value cross_over in
        let cu = TrendM.value cross_under in
        let signal =
          match value_option co, value_option cu with
          | Some true, _ ->
            incr buys;
            "  <-- GOLDEN CROSS (buy)"
          | _, Some true ->
            incr sells;
            "  <-- DEATH CROSS (sell)"
          | _ -> ""
        in
        Printf.printf
          "%-4d %-6s %-6s %-6s %-9s %-9s%s\n"
          i
          (pp_float (TrendM.value close))
          (pp_float (TrendM.value fast))
          (pp_float (TrendM.value slow))
          (pp_bool co)
          (pp_bool cu)
          signal)
     prices;
  Printf.printf "\nSummary: %d golden cross (buy), %d death cross (sell).\n" !buys !sells
;;
