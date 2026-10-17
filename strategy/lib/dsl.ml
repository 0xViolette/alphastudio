include Dsl_intf

module Make () : S = struct
  open Ast

  type set = Machine.set = Set : 'a signal * 'a -> set
  type 'a signal = 'a Ast.signal

  let g = Graph.create ()
  let undefined () = Graph.undefined g
  let const x = Graph.const g x
  let input () = Graph.input g
  let ginput () = Graph.ginput g
  let pre s = Graph.pre g s

  let rec delay n s =
    match n with
    | 0 -> s
    | n when n > 0 -> delay (n - 1) (pre s)
    | _ -> invalid_arg "delay: cannot be negative"
  ;;

  let rec window length s =
    if length < 0
    then invalid_arg "window: lookback cannot be negative";
    if length = 0 then [] else s :: window (length - 1) (pre s)
  ;;

  let recurrence f = Graph.recurrence g f
  let map f a = Graph.map g f a
  let map2 f a b = Graph.map2 g f a b
  let gmap f a = Graph.gmap g f a
  let gmap2 f a b = Graph.gmap2 g f a b

  (* -----------Applicative laws----------- *)

  let pure x = Graph.pure g x

  let ( <*> ) f a =
    gmap2 (fun f a -> Option.map2 (fun f a -> f a) f a) f a
  ;;

  (* -------------------------------------- *)

  let lift f a = pure f <*> a
  let lift2 f a b = pure f <*> a <*> b

  let gmap3 f a b c =
    gmap2
      (fun ab c ->
         match ab with
         | Some (a, b) -> f a b c
         | _ -> None)
      (gmap2 (fun a b -> Some (a, b)) a b)
      c
  ;;

  let cond c t e =
    gmap3
      (fun c t e ->
         match c with
         | Some true -> t
         | Some false -> e
         | None -> None)
      c
      t
      e
  ;;

  let read f = gmap (fun _ -> f ()) (undefined ())

  (*unary operations*)
  let neg = map Float.neg
  let abs = map Float.abs
  let sqrt = map Float.sqrt
  let floor = map Float.floor
  let ceil = map Float.ceil
  let not = lift Bool.not

  (*binary operations*)
  let add = map2 Float.add
  let sub = map2 Float.sub
  let mul = map2 Float.mul
  let div = map2 Float.div
  let eq a b = lift2 ( = ) a b
  let neq a b = lift2 ( <> ) a b
  let lt = lift2 ( < )
  let le = lift2 ( <= )
  let gt = lift2 ( > )
  let ge = lift2 ( >= )
  let min = map2 Float.min
  let max = map2 Float.max
  let and_ = lift2 ( && )
  let or_ = lift2 ( || )
  let is_pending s = gmap (fun a -> Some (Option.is_none a)) s

  module Ops = struct
    let ( ! ) = const
    let ( + ) = add
    let ( - ) = sub
    let ( * ) = mul
    let ( / ) = div
    let ( < ) = lt
    let ( <= ) = le
    let ( > ) = gt
    let ( >= ) = ge
    let ( && ) = and_
    let ( || ) = or_
    let ( = ) = eq
    let ( <> ) = neq
    let abs = abs
    let sqrt = sqrt
    let not = not
    let floor = floor
    let ceil = ceil
  end

  let tick =
    recurrence (fun prev ->
      cond (is_pending prev) (const 0.) Ops.(prev + !1.))
  ;;

  let value_when event signal =
    recurrence (fun prev -> cond event signal prev)
  ;;

  module type Machine = sig
    val step : set list -> unit
    val value : 'a signal -> 'a option
  end

  let compile () =
    if g.compiled
    then failwith "already compiled"
    else (
      g.compiled <- true;
      let r = Machine.compile g in
      (module struct
        let step = Machine.step r
        let value s = Machine.value r s
      end : Machine))
  ;;

  module Indicator = struct
    let sum n s =
      let acc =
        recurrence (fun prev ->
          cond (is_pending prev) s Ops.(prev + s))
      in
      recurrence (fun prev ->
        cond
          (is_pending prev)
          (cond (is_pending (delay (n - 1) s)) (undefined ()) acc)
          Ops.(prev + s - delay n s))
    ;;

    let sma n s = Ops.(sum n s / !(Float.of_int n))
    let min = min
    let max = max

    let ema n s =
      let alpha = Float.div 2. (float_of_int (n + 1)) in
      recurrence (fun prev_ema ->
        cond
          (is_pending prev_ema)
          (sma n s)
          Ops.((s * !alpha) + (prev_ema * !(Float.sub 1.0 alpha))))
    ;;

    let crossover a b = Ops.(pre a < pre b && a > b)
    let crossunder a b = Ops.(pre a > pre b && a < b)

    let highest n s =
      window n s |> List.fold_left max @@ const Float.neg_infinity
    ;;

    let lowest n s =
      window n s |> List.fold_left min @@ const Float.infinity
    ;;

    let bars_since c =
      recurrence Ops.(fun prev -> cond c !0. (prev + !1.0))
    ;;
  end
end
