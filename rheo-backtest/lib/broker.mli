type t

val create :
  lot_size:float -> initial_balance:float -> ?costs:Costs.t -> unit -> t

val initial_balance : t -> float
val cash : t -> float
val realised_pnl : t -> float
val net_lots : t -> float
val net_dir : t -> float
val signed_net_lots : t -> float
val entry_price : t -> float option
val avg_entry_price : t -> float option

(* Replace the working order set atomically. Zero-quantity exits are retained
   so they can activate after an entry fills on the same bar.
   @raise Invalid_argument
   for duplicate ids, negative or non-finite targets, non-positive or
   non-finite prices, and non-finite computed quantities. No broker state is
   changed when validation fails. *)
val replace_orders : t -> Order.request list -> Order.request list

(* Execute orders installed on the previous bar. After every fill, remaining
    quantities are reconciled against the new portfolio. Same-direction entries
    are blocked; opposite entries close or reverse the existing position. *)
val execute_bar : t -> Bar.t -> Order.fill list
