open Order

type working = { request : Order.request; seq : int; side : side; lots : float }

type t = {
  lot_size : float;
  costs : Costs.t;
  mutable portfolio : Portfolio.t;
  mutable next_seq : int;
  mutable orders : working list;
}

let create ~lot_size ~initial_balance ?(costs = Costs.none) () =
  {
    lot_size;
    costs;
    portfolio = Portfolio.create initial_balance;
    next_seq = 0;
    orders = [];
  }

let initial_balance t = Portfolio.initial_balance t.portfolio
let cash t = Portfolio.cash t.portfolio
let realised_pnl t = Portfolio.realised_pnl t.portfolio
let net_lots t = Portfolio.net_lots t.portfolio
let net_dir t = Portfolio.net_dir t.portfolio
let signed_net_lots t = Portfolio.signed_net_lots t.portfolio
let entry_price t = Portfolio.entry_price t.portfolio
let avg_entry_price t = Portfolio.avg_entry_price t.portfolio

let side_of = function
  | Enter_long _ | Exit_short -> Buy
  | Enter_short _ | Exit_long -> Sell

let lots_for_action action ~net =
  match action with
  | Enter_long target -> if net > 0. then target else target -. net
  | Enter_short target -> if net < 0. then target else target +. net
  | Exit_long -> if net > 0. then net else 0.
  | Exit_short -> if net < 0. then -.net else 0.

let cancel t id =
  t.orders <- List.filter (fun o -> not (String.equal o.request.id id)) t.orders

let place t request lots =
  cancel t request.id;
  let order =
    { request; seq = t.next_seq; side = side_of request.action; lots }
  in
  t.orders <- t.orders @ [ order ];
  t.next_seq <- t.next_seq + 1

let modify t id ~lots =
  t.orders <-
    List.map
      (fun o -> if String.equal o.request.id id then { o with lots } else o)
      t.orders

let invalid_request request message =
  invalid_arg
    (Printf.sprintf "Broker.replace_orders: order %S %s" request.Order.id
       message)

let validate_requests requests =
  let ids = Hashtbl.create (List.length requests) in
  List.iter
    (fun request ->
      if Hashtbl.mem ids request.Order.id then
        invalid_request request "has a duplicate id";
      Hashtbl.add ids request.Order.id ();
      (match request.Order.action with
      | Enter_long target | Enter_short target ->
          if (not (Float.is_finite target)) || target < 0. then
            invalid_request request
              (Printf.sprintf "has an invalid target %.10g" target)
      | Exit_long | Exit_short -> ());
      match request.Order.kind with
      | Market -> ()
      | Limit price | Stop price ->
          if (not (Float.is_finite price)) || price <= 0. then
            invalid_request request
              (Printf.sprintf "has an invalid price %.10g" price))
    requests

let is_exit = function Exit_long | Exit_short -> true | _ -> false

let replace_orders t requests =
  validate_requests requests;
  let net = signed_net_lots t in
  let active =
    List.filter_map
      (fun request ->
        let lots = lots_for_action request.action ~net in
        if not (Float.is_finite lots) then
          invalid_request request "has a non-finite computed quantity";
        if lots > 0. || is_exit request.action then Some (request, lots) else None)
      requests
  in
  t.orders <- [];
  List.iter (fun (request, lots) -> place t request lots) active;
  List.map fst active

let bar_path (b : Bar.t) =
  if b.high -. b.open_ <= b.open_ -. b.low then `OHLC else `OLHC

(* a candidate execution on the current bar: [path_dist] is the distance
   along the bar's assumed path at which the fill happens (a price distance,
   not a time); [open_priority] sequences fills that all happen at the open *)
type execution = { price : float; path_dist : float; open_priority : float }

(* when the open gaps through several stops at once they all fill at the
   open; sequence them by how far each level lies beyond the open along the
   incoming path (the level hit first on the way down/up fills first) *)
let fill_on_bar (b : Bar.t) (o : working) : execution option =
  let dist p =
    match bar_path b with
    | `OHLC ->
        if p >= b.open_ then p -. b.open_ else b.high -. b.open_ +. (b.high -. p)
    | `OLHC ->
        if p <= b.open_ then b.open_ -. p else b.open_ -. b.low +. (p -. b.low)
  in
  let at_open open_priority =
    Some { price = b.open_; path_dist = 0.; open_priority }
  in
  let reached p =
    if b.low <= p && p <= b.high then
      Some { price = p; path_dist = dist p; open_priority = 0. }
    else None
  in
  match (o.request.kind, o.side) with
  (* Price-triggered gap fills retain incoming-path priority; market orders
     follow them, independently of direction. *)
  | Market, _ ->
      Some { price = b.open_; path_dist = 0.; open_priority = Float.infinity }
  | Limit p, Buy | Stop p, Sell ->
      if b.open_ <= p then at_open (-.p) else reached p
  | Limit p, Sell | Stop p, Buy -> if b.open_ >= p then at_open p else reached p

let sync_orders t =
  let net = signed_net_lots t in
  List.iter
    (fun o ->
      let lots = lots_for_action o.request.action ~net in
      if is_exit o.request.action then modify t o.request.id ~lots
      else if lots > 0. then modify t o.request.id ~lots
      else cancel t o.request.id)
    t.orders

let execute_bar t bar =
  let path_cursor = ref 0. in
  let next_fill () =
    let net_dir = net_dir t in
    let blocked o =
      o.lots <= 0.
      || match o.side with Buy -> net_dir > 0. | Sell -> net_dir < 0.
    in
    let candidates =
      List.filter_map
        (fun o ->
          if blocked o then None
          else
            match fill_on_bar bar o with
            | Some exec when exec.path_dist >= !path_cursor -> Some (exec, o)
            | _ -> None)
        t.orders
    in
    let earlier (e1, o1) (e2, o2) =
      compare
        (e1.path_dist, e1.open_priority, o1.seq)
        (e2.path_dist, e2.open_priority, o2.seq)
    in
    match List.sort earlier candidates with
    | [] -> None
    | (exec, o) :: _ ->
        path_cursor := exec.path_dist;
        cancel t o.request.id;
        (* the trigger logic above runs on the raw price; costs only move the
         executed price (slippage, against the order) and debit the fee *)
        let res =
          t.costs.apply
            {
              Costs.side = o.side;
              kind = o.request.kind;
              price = exec.price;
              lots = o.lots;
              lot_size = t.lot_size;
              bar;
            }
        in
        let dir = Float.of_int (sign_of_side o.side) in
        let price = exec.price +. (dir *. res.slippage) in
        let fill =
          {
            id = o.request.id;
            side = o.side;
            price;
            lots = o.lots;
            fees = res.fee;
          }
        in
        t.portfolio <-
          Portfolio.apply_fill ~lot_size:t.lot_size t.portfolio fill;
        Some fill
  in
  let rec collect fills =
    match next_fill () with
    | None -> List.rev fills
    | Some fill ->
        sync_orders t;
        collect (fill :: fills)
  in
  collect []
