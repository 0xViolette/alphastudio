module type S = sig
  type 'a signal
  type set = Set : 'a signal * 'a -> set

  val mul : float signal -> float signal -> float signal
  val tick : float signal

  (* stepping and reading are only meaningful on a frozen graph, so both
     live on the machine [compile] returns *)
  module type MACHINE = sig
    val step : set list -> unit
    val value : 'a signal -> 'a option
  end

  val compile : unit -> (module MACHINE)
  val const : float -> float signal
  val pure : 'a -> 'a signal
  val undefined : unit -> 'a signal
  val input : unit -> float signal
  val ginput : unit -> 'a signal
  val pre : 'a signal -> 'a signal
  val delay : int -> 'a signal -> 'a signal
  val window : int -> 'a signal -> 'a signal list
  val recurrence : (float signal -> float signal) -> float signal

  module Ops : sig
    val ( ! ) : float -> float signal
    val ( + ) : float signal -> float signal -> float signal
    val ( * ) : float signal -> float signal -> float signal
    val ( - ) : float signal -> float signal -> float signal
    val ( / ) : float signal -> float signal -> float signal
    val ( < ) : float signal -> float signal -> bool signal
    val ( <= ) : float signal -> float signal -> bool signal
    val ( > ) : float signal -> float signal -> bool signal
    val ( >= ) : float signal -> float signal -> bool signal
    val ( && ) : bool signal -> bool signal -> bool signal
    val ( || ) : bool signal -> bool signal -> bool signal
    val ( = ) : 'a signal -> 'a signal -> bool signal
    val ( <> ) : 'a signal -> 'a signal -> bool signal
    val abs : float signal -> float signal
    val not : bool signal -> bool signal
    val sqrt : float signal -> float signal
    val floor : float signal -> float signal
    val ceil : float signal -> float signal
  end

  val cond : bool signal -> 'a signal -> 'a signal -> 'a signal
  val value_when : bool signal -> float signal -> float signal
  val is_pending : 'a signal -> bool signal
  val map : (float -> float) -> float signal -> float signal

  val map2
    :  (float -> float -> float)
    -> float signal
    -> float signal
    -> float signal

  val gmap : ('a option -> 'b option) -> 'a signal -> 'b signal

  val gmap2
    :  ('a option -> 'b option -> 'c option)
    -> 'a signal
    -> 'b signal
    -> 'c signal

  val lift : ('a -> 'b) -> 'a signal -> 'b signal
  val lift2 : ('a -> 'b -> 'c) -> 'a signal -> 'b signal -> 'c signal
  val ( <*> ) : ('a -> 'b) signal -> 'a signal -> 'b signal
  val read : (unit -> 'b option) -> 'b signal

  module Indicator : sig
    val max : float signal -> float signal -> float signal
    val min : float signal -> float signal -> float signal
    val sma : int -> float signal -> float signal
    val ema : int -> float signal -> float signal
    val crossover : float signal -> float signal -> bool signal
    val crossunder : float signal -> float signal -> bool signal
    val highest : int -> float signal -> float signal
    val lowest : int -> float signal -> float signal
    val bars_since : bool signal -> float signal
  end
end

module type Strategy = sig
  module Make : functor () -> S
end
