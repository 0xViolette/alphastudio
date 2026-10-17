module Make () = struct
  module S' = Strategy.Make ()

  let broker = Broker.create ~lot_size:50. ~initial_balance:3_000_000.

  include S'

  let open_ : float signal = S'.input ()
  let high : float signal = S'.input ()
  let low : float signal = S'.input ()
  let close : float signal = S'.input ()
  let lot_size = broker.lot_size

  module Account = struct
    let cash : float signal = S'.input ()
    let realised_pnl : float signal = S'.input ()
    let initial_balance : float signal = S'.input ()
  end

  module Position = struct
    let open_lots : float signal = S'.input ()
    let direction : float signal = S'.input ()
    let entry_price = read (fun _ -> Broker.entry_price broker)

    let avg_entry_price =
      read (fun _ -> Broker.avg_entry_price broker)
    ;;
  end

  module Indicator = struct
    include Indicator
    open Ops

    let tr =
      let prev_close = pre close in
      max
        (high - low)
        (max (abs (high - prev_close)) (abs (low - prev_close)))
    ;;

    let atr n =
      let alpha = 1. /. float_of_int n in
      recurrence (fun p ->
        cond
          (is_pending p)
          (sma n tr)
          ((tr * !alpha) + (p * !(1. -. alpha))))
    ;;
  end

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

  let enter_long id target kind when_ =
    order id (fun t -> Order.Enter_long t) target kind when_
  ;;

  let enter_short id target kind when_ =
    order id (fun t -> Order.Enter_short t) target kind when_
  ;;

  let exit_long id kind when_ =
    order id (fun _ -> Order.Exit_long) (const 0.) kind when_
  ;;

  let exit_short id kind when_ =
    order id (fun _ -> Order.Exit_short) (const 0.) kind when_
  ;;

  let backtest
        ?(probes : (string * float signal) list = [])
        ?(on_bar = fun (_ : Report.t) -> ())
        (orders : Order.request signal list)
    =
    let module Machine = (val compile ()) in
    let rec loop (resting : Order.request list) bars =
      match bars () with
      | Seq.Nil -> prerr_endline "End of stream"
      | Seq.Cons (bar, rest) ->
        Broker.start_bar broker bar;
        let fills = Oms.fill_resting_orders broker resting in
        Broker.end_bar broker;
        Machine.step
          [ Set (open_, bar.open_)
          ; Set (high, bar.high)
          ; Set (low, bar.low)
          ; Set (close, bar.close)
          ; Set (Position.direction, Broker.net_dir broker)
          ; Set (Position.open_lots, Broker.net_lots broker)
          ; Set
              (Account.initial_balance, broker.account.initial_balance)
          ; Set (Account.cash, broker.account.cash)
          ; Set (Account.realised_pnl, Broker.realised_pnl broker)
          ];
        let submitted =
          Oms.place_orders
            broker
            (List.filter_map Machine.value orders)
        in
        let is_new (d : Order.request) =
          Bool.not
            (List.exists
               (fun (r : Order.request) -> String.equal r.id d.id)
               resting)
        in
        on_bar
          { Report.bar
          ; fills
          ; placed = List.filter is_new submitted
          ; probes =
              List.map (fun (name, s) -> name, Machine.value s) probes
          };
        loop submitted rest
    in
    loop [] (Feed.stdin_bars ())
  ;;
end
