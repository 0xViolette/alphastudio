module Make () = struct
  module D = Rheo.Dsl.Make ()
  include D
  module Env = Env.Make (D)
  include Env
  module Ta = RheoTa.Make (D) (Env)

  let observations = ref []
  let order_signals = ref []
  let observe s name = observations := (s, name) :: !observations

  let kind_signal = function
    | Order.Market -> pure Order.Market
    | Limit p -> lift (fun p -> Order.Limit p) p
    | Stop p -> lift (fun p -> Order.Stop p) p

  let new_order id action target kind when_ =
    let make_req =
      pure (fun target kind -> { Order.id; action = action target; kind })
    in
    let req = make_req <*> target <*> kind_signal kind in
    let order = cond when_ req (undefined ()) in
    order_signals := order :: !order_signals

  let enter_long id = new_order id (fun t -> Order.Enter_long t)
  let enter_short id = new_order id (fun t -> Order.Enter_short t)
  let exit_long id = new_order id (fun _ -> Order.Exit_long) (const 0.)
  let exit_short id = new_order id (fun _ -> Order.Exit_short) (const 0.)

  let set_inputs ~(config : Config.t) ~broker (b : Bar.t) =
    [
      Set (open_, b.open_);
      Set (high, b.high);
      Set (low, b.low);
      Set (close, b.close);
      Set (lot_size, config.lot_size);
      Set (Position.direction, Broker.net_dir broker);
      Set (Position.open_lots, Broker.net_lots broker);
      Set
        ( Position.entry_price,
          Option.value (Broker.entry_price broker) ~default:nan );
      Set
        ( Position.avg_entry_price,
          Option.value (Broker.avg_entry_price broker) ~default:nan );
      Set (Account.initial_balance, Broker.initial_balance broker);
      Set (Account.cash, Broker.cash broker);
      Set (Account.realised_pnl, Broker.realised_pnl broker);
    ]

  let started = ref false

  let run ~(config : Config.t) ?(on_bar = fun (_ : Bar_report.t) -> ()) bars =
    if !started then
      invalid_arg "Backtest.run: a Backtest instance can only be run once";
    started := true;
    let broker =
      Broker.create ~lot_size:config.lot_size
        ~initial_balance:config.initial_balance ~costs:config.costs ()
    in
    let orders = List.rev !order_signals in
    let observations = List.rev !observations in
    let module Engine = (val compile ()) in
    let rec loop (previous_requests : Order.request list) bars =
      match bars () with
      | Seq.Nil -> ()
      | Seq.Cons (bar, rest) ->
          let fills = Broker.execute_bar broker bar in
          Engine.step (set_inputs ~config ~broker bar);
          let active_requests =
            orders
            |> List.filter_map Engine.value
            |> Broker.replace_orders broker
          in
          let is_new (request : Order.request) =
            not
              (List.exists
                 (fun (previous : Order.request) ->
                   String.equal previous.id request.id)
                 previous_requests)
          in
          on_bar
            {
              Bar_report.bar;
              fills;
              new_orders = active_requests |> List.filter is_new;
              observations =
                List.map (fun (s, name) -> (name, Engine.value s)) observations;
            };
          loop active_requests rest
    in
    loop [] bars

  let run_csv_cli (config : Config.t) =
    let observations = List.rev !observations in
    let t0 = Sys.time () in
    Printf.printf "%s\n" (Bar_report.csv_header (List.map snd observations));
    run ~config
      ~on_bar:(fun r ->
        Printf.printf "%s\n" (Bar_report.csv_row ~lot_size:config.lot_size r))
      (Feed.stdin_bars ~from_ts:config.from_ts ~till_ts:config.till_ts ());
    prerr_endline "End of stream";
    Printf.eprintf "Execution time: %.4f seconds\n" (Sys.time () -. t0)
end
