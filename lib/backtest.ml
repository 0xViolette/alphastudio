module Make (C : sig
  val lot_size : float
  val initial_balance : float
  val costs : Costs.t
end) () =
struct
  module D = Rheo.Dsl.Make ()
  include D

  let broker =
    Broker.create
      ~lot_size:C.lot_size
      ~initial_balance:C.initial_balance
      ~costs:C.costs
      ()
  ;;

  module Env =
    Env.Make
      (D)
      (struct
        let broker = broker
      end)

  include Env
  module Indicator = Ta.Make (D) (Env)

  let kind_signal = function
    | Order.Market -> pure Order.Market
    | Limit p -> lift (fun p -> Order.Limit p) p
    | Stop p -> lift (fun p -> Order.Stop p) p
  ;;

  let order id action target kind when_ =
    let make_req =
      pure (fun target kind ->
        { Order.id; action = action target; kind })
    in
    let req = make_req <*> target <*> kind_signal kind in
    cond when_ req (undefined ())
  ;;

  let enter_long id = order id (fun t -> Order.Enter_long t)
  let enter_short id = order id (fun t -> Order.Enter_short t)
  let exit_long id = order id (fun _ -> Order.Exit_long) (const 0.)
  let exit_short id = order id (fun _ -> Order.Exit_short) (const 0.)

  let set_inputs (b : Codec.Ohlc.bar) =
    [ Set (open_, b.open_)
    ; Set (high, b.high)
    ; Set (low, b.low)
    ; Set (close, b.close)
    ; Set (Position.direction, Broker.net_dir broker)
    ; Set (Position.open_lots, Broker.net_lots broker)
    ; Set (Account.initial_balance, broker.account.initial_balance)
    ; Set (Account.cash, broker.account.cash)
    ; Set (Account.realised_pnl, Broker.realised_pnl broker)
    ]
  ;;

  let process_bar
        (bar : Codec.Ohlc.bar)
        (resting : Order.request list)
    =
    Broker.start_bar broker bar;
    let fills = Oms.fill_resting_orders broker resting in
    Broker.end_bar broker;
    fills
  ;;

  let backtest
        ?(from_ts = None)
        ?(till_ts = None)
        ?(probes : (string * float signal) list = [])
        ?(on_bar = fun (_ : Report.t) -> ())
        (orders : Order.request signal list)
    =
    let module Engine = (val compile ()) in
    let rec loop (resting : Order.request list) bars =
      match bars () with
      | Seq.Nil -> prerr_endline "End of stream"
      | Seq.Cons (bar, rest) ->
        let fills = process_bar bar resting in
        Engine.step (set_inputs bar);
        let next_resting =
          orders
          |> List.filter_map Engine.value
          |> Oms.place_orders broker
        in
        let is_new (d : Order.request) =
          not
            (List.exists
               (fun (r : Order.request) -> String.equal r.id d.id)
               resting)
        in
        on_bar
          { Report.bar
          ; fills
          ; placed = next_resting |> List.filter is_new
          ; probes =
              List.map (fun (name, s) -> name, Engine.value s) probes
          };
        loop next_resting rest
    in
    loop [] (Feed.stdin_bars ~from_ts ~till_ts ())
  ;;
end
