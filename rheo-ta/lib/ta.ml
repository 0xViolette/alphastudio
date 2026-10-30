module Make
    (D : Rheo.Dsl.S)
    (Bars : sig
      val open_ : float D.signal
      val high : float D.signal
      val low : float D.signal
      val close : float D.signal
    end) =
struct
  open D
  open Bars
  open Ops

  let sum n s =
    let acc = recurrence (fun prev -> cond (is_pending prev) s (prev + s)) in
    recurrence (fun prev ->
        cond (is_pending prev)
          (cond (is_pending (delay (Int.sub n 1) s)) (undefined ()) acc)
          (prev + s - delay n s))

  let sma n s = sum n s / !(Float.of_int n)

  let ema n s =
    let alpha = Float.div 2. (float_of_int (Int.add n 1)) in
    recurrence (fun prev_ema ->
        cond (is_pending prev_ema) (sma n s)
          ((s * !alpha) + (prev_ema * !(Float.sub 1.0 alpha))))

  let crossover a b = pre a < pre b && a > b
  let crossunder a b = pre a > pre b && a < b
  let highest n s = window n s |> List.fold_left max @@ const Float.neg_infinity
  let lowest n s = window n s |> List.fold_left min @@ const Float.infinity
  let bars_since c = recurrence (fun prev -> cond c !0. (prev + !1.0))

  let tr =
    let prev_close = pre close in
    max (high - low) (max (abs (high - prev_close)) (abs (low - prev_close)))

  let atr n =
    let alpha = 1. /. float_of_int n in
    recurrence (fun p ->
        cond (is_pending p) (sma n tr) ((tr * !alpha) + (p * !(1. -. alpha))))
end
