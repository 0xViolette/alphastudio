type t = {
  costs : Costs.t;
  params : Costs.params;
  lot_size : float;
  initial_balance : float;
  from_ts : int64 option;
  till_ts : int64 option; (* exclusive: first midnight IST after the till date *)
}

let default_lot_size = 65.
let default_initial_balance = 3_000_000.
