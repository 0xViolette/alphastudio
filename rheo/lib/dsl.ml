include Dsl_intf

module Make () : S = struct
  open Ast

  type set = Machine.set = Set : 'a signal * 'a -> set
  type 'a signal = 'a Ast.signal

  (* -------------------------------------------------------------------------- *)
  (* Graph construction                                                         *)
  (* -------------------------------------------------------------------------- *)

  (* Each DSL instance owns an independent graph. *)
  let graph = Graph.create ()
  let undefined () = Graph.undefined graph
  let const x = Graph.const graph x
  let input () = Graph.input graph
  let ginput () = Graph.ginput graph
  let map f s = Graph.map graph f s
  let map2 f s1 s2 = Graph.map2 graph f s1 s2
  let gmap f s = Graph.gmap graph f s
  let gmap2 f s1 s2 = Graph.gmap2 graph f s1 s2
  let recurrence f = Graph.recurrence graph f

  (* -------------------------------------------------------------------------- *)
  (* Temporal primitives                                                        *)
  (* -------------------------------------------------------------------------- *)

  let pre signal = Graph.pre graph signal

  (** [delay n signal] refers to the value of [signal] from [n] ticks ago. A
      delay of zero is the identity. *)
  let rec delay n s =
    match n with
    | 0 -> s
    | n when n > 0 -> delay (n - 1) (pre s)
    | _ -> invalid_arg "delay: cannot be negative"

  (** [window n signal] returns the current signal followed by its preceding
      [n - 1] values, in reverse chronological order.

      A window of zero has no elements. *)
  let rec window length s =
    if length < 0 then invalid_arg "window: lookback cannot be negative";
    if length = 0 then [] else s :: window (length - 1) (pre s)

  (* -------------------------------------------------------------------------- *)
  (* Applicative composition                                                    *)
  (* -------------------------------------------------------------------------- *)

  let pure x = Graph.pure graph x
  let ( <*> ) f a = gmap2 (fun f a -> Option.map2 (fun f a -> f a) f a) f a
  let lift f a = pure f <*> a
  let lift2 f a b = pure f <*> a <*> b

  let gmap3 f a b c =
    gmap2
      (fun ab c -> match ab with Some (a, b) -> f a b c | _ -> None)
      (gmap2 (fun a b -> Some (a, b)) a b)
      c

  (* -------------------------------------------------------------------------- *)
  (* Control flow and signal state                                              *)
  (* -------------------------------------------------------------------------- *)

  let cond c t e =
    gmap3
      (fun c t e ->
        match c with Some true -> t | Some false -> e | None -> None)
      c t e

  let read f = gmap (fun _ -> f ()) (undefined ())
  let is_pending s = gmap (fun a -> Some (Option.is_none a)) s

  module Ops = struct
    (** Signal-level operations.

        These functions construct expressions in the signal graph rather than
        evaluating values immediately. Unary and binary operations are lifted
        over signals through the corresponding combinators. *)

    (* Unary Numeric operations *)
    let neg = map Float.neg
    let abs = map Float.abs
    let sqrt = map Float.sqrt
    let floor = map Float.floor
    let ceil = map Float.ceil

    (* Boolean operations *)
    let not = lift Bool.not
    let and_ = lift2 ( && )
    let or_ = lift2 ( || )

    (* Arithmetic operations *)
    let add = map2 Float.add
    let sub = map2 Float.sub
    let mul = map2 Float.mul
    let div = map2 Float.div
    let min = map2 Float.min
    let max = map2 Float.max

    (* Comparisons *)
    let lt = lift2 ( < )
    let le = lift2 ( <= )
    let gt = lift2 ( > )
    let ge = lift2 ( >= )
    let eq a b = lift2 ( = ) a b
    let neq a b = lift2 ( <> ) a b

    (** Infix interface for composing signal expressions.

        [!] embeds a constant; the remaining operators construct signal-level
        operations. *)

    let ( ! ) = const
    let ( + ) = add
    let ( - ) = sub
    let ( * ) = mul
    let ( / ) = div
    let ( < ) = lt
    let ( <= ) = le
    let ( > ) = gt
    let ( >= ) = ge
    let ( = ) = eq
    let ( <> ) = neq
    let ( && ) = and_
    let ( || ) = or_
  end

  (** A zero-based tick counter. *)
  let tick =
    recurrence (fun prev -> cond (is_pending prev) (const 0.) Ops.(prev + !1.))

  (** Hold the most recent [signal] value for which [event] was true. *)
  let value_when event signal = recurrence (fun prev -> cond event signal prev)

  (* -------------------------------------------------------------------------- *)
  (* Compilation                                                                *)
  (* -------------------------------------------------------------------------- *)

  module type Machine = sig
    val step : set list -> unit
    val value : 'a signal -> 'a option
  end

  (** Compile this graph into an executable machine.

      Compilation is one-shot: a DSL instance cannot be compiled twice. *)
  let compile () =
    if graph.compiled then failwith "already compiled"
    else (
      graph.compiled <- true;
      let r = Machine.compile graph in
      (module struct
        let step = Machine.step r
        let value s = Machine.value r s
      end : Machine))
end

(* module Indicator = struct *)
(*   let sum n s = *)
(*     let acc = *)
(*       recurrence (fun prev -> cond (is_pending prev) s Ops.(prev + s)) *)
(*     in *)
(*     recurrence (fun prev -> *)
(*         cond (is_pending prev) *)
(*           (cond (is_pending (delay (n - 1) s)) (undefined ()) acc) *)
(*           Ops.(prev + s - delay n s)) *)
(**)
(*   let sma n s = Ops.(sum n s / !(Float.of_int n)) *)
(**)
(*   let ema n s = *)
(*     let alpha = Float.div 2. (float_of_int (n + 1)) in *)
(*     recurrence (fun prev_ema -> *)
(*         cond (is_pending prev_ema) (sma n s) *)
(*           Ops.((s * !alpha) + (prev_ema * !(Float.sub 1.0 alpha)))) *)
(**)
(*   let crossover a b = Ops.(pre a < pre b && a > b) *)
(*   let crossunder a b = Ops.(pre a > pre b && a < b) *)
(**)
(*   let highest n s = *)
(*     window n s |> List.fold_left max @@ const Float.neg_infinity *)
(**)
(*   let lowest n s = window n s |> List.fold_left min @@ const Float.infinity *)
(*   let bars_since c = recurrence Ops.(fun prev -> cond c !0. (prev + !1.0)) *)
(* end *)
