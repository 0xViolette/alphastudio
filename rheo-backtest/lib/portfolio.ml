module Position = struct
  type side = Long | Short
  type t = { price : float; lots : float; side : side }

  let dir t = match t.side with Long -> 1. | Short -> -1.
end

module Positions = struct
  type t = Position.t list

  let create () : t = []
  let dir (t : t) = match t with [] -> 0. | p :: _ -> Position.dir p

  let net_lots (t : t) =
    List.fold_left (fun acc (p : Position.t) -> acc +. p.lots) 0. t

  let entry_price (t : t) =
    match t with lot :: _ -> Some lot.price | [] -> None

  let avg_entry_price (t : t) =
    match t with
    | [] -> None
    | positions ->
        Some
          (List.fold_left
             (fun acc (p : Position.t) -> acc +. (p.price *. p.lots))
             0. positions
          /. net_lots t)

  let add ~lot_size (t : t) (p : Position.t) =
    let rec consume t (p : Position.t) delta_pnl =
      if p.lots = 0. then (t, delta_pnl)
      else
        match t with
        | [] -> ([ p ], delta_pnl)
        | pos :: _ when Position.dir pos = Position.dir p ->
            (t @ [ p ], delta_pnl)
        | pos :: rest ->
            let matched_lots = Float.min pos.lots p.lots in
            let delta_pnl =
              delta_pnl
              +. Position.dir pos *. matched_lots *. lot_size
                 *. (p.price -. pos.price)
            in
            let pos' = { pos with lots = pos.lots -. matched_lots } in
            let p' = { p with lots = p.lots -. matched_lots } in
            consume (if pos'.lots = 0. then rest else pos' :: rest) p' delta_pnl
    in
    consume t p 0.
end

type t = {
  initial_balance : float;
  cash : float;
  realised_pnl : float;
  positions : Positions.t;
}

let create initial_balance =
  {
    initial_balance;
    cash = initial_balance;
    realised_pnl = 0.;
    positions = Positions.create ();
  }

let initial_balance t = t.initial_balance
let cash t = t.cash
let realised_pnl t = t.realised_pnl
let net_lots t = Positions.net_lots t.positions
let net_dir t = Positions.dir t.positions
let signed_net_lots t = net_dir t *. net_lots t
let entry_price t = Positions.entry_price t.positions
let avg_entry_price t = Positions.avg_entry_price t.positions

let apply_fill ~lot_size t (fill : Order.fill) =
  let side =
    match fill.side with
    | Order.Buy -> Position.Long
    | Order.Sell -> Position.Short
  in
  let positions, delta_pnl =
    Positions.add ~lot_size t.positions
      { lots = fill.lots; price = fill.price; side }
  in
  let delta_balance = delta_pnl -. fill.fees in
  {
    t with
    cash = t.cash +. delta_balance;
    realised_pnl = t.realised_pnl +. delta_balance;
    positions;
  }
