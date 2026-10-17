(* Trading-flavored indicators built on top of the language-level
   [Dsl.Indicator] set; need the bar signals to compute ranges. *)
module Make
    (D : Dsl.S)
    (Bars : sig
       val open_ : float D.signal
       val high : float D.signal
       val low : float D.signal
       val close : float D.signal
     end) =
struct
  include D.Indicator
  open D
  open Ops
  open Bars

  let tr =
    let prev_close = pre close in
    max
      (high - low)
      (max (abs (high - prev_close)) (abs (low - prev_close)))
  ;;

  let atr n =
    let alpha = 1. /. float_of_int n in
    recurrence (fun p ->
      cond
        (is_pending p)
        (sma n tr)
        ((tr * !alpha) + (p * !(1. -. alpha))))
  ;;
end
