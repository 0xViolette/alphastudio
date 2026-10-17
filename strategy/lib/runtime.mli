type src =
  | F of int
  | E of int

val src_of : 'a Language.signal -> src

type instr =
  | IPrev of int * int
  | IPrevE of int * int
  | ICopy of int * int
  | IUnbox of int * int
  | IMap of int * (float -> float) * int
  | IMap2 of int * (float -> float -> float) * int * int
  | IGMap of int * (Obj.t -> Obj.t) * src
  | IGmap2 of int * (Obj.t -> Obj.t -> Obj.t) * src * src

type t =
  { instrs : instr array
  ; mutable float_curs : float array
  ; mutable float_prevs : float array
  ; mutable effect_curs : Obj.t array
  ; mutable effect_prevs : Obj.t array
  ; inputs : src array
  ; graph_id : int
  }

val compile : Graph.t -> t
val read_src : t -> src -> Obj.t
val exec : t -> instr -> unit

type set = Set : 'a Language.signal * 'a -> set

val step : t -> set list -> unit
val value : t -> 'a Language.signal -> 'a option
