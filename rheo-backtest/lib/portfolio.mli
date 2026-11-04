module Position : sig
  type side = Long | Short
  type t = { price : float; lots : float; side : side }

  val dir : t -> float
end

module Positions : sig
  type t = Position.t list

  val create : unit -> t
  val dir : t -> float
  val net_lots : t -> float
  val entry_price : t -> float option
  val avg_entry_price : t -> float option
  val add : lot_size:float -> t -> Position.t -> t * float
end

type t

val create : float -> t
val initial_balance : t -> float
val cash : t -> float
val realised_pnl : t -> float
val net_lots : t -> float
val net_dir : t -> float
val signed_net_lots : t -> float
val entry_price : t -> float option
val avg_entry_price : t -> float option
val apply_fill : lot_size:float -> t -> Order.fill -> t
