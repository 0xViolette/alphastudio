(* The trading environment a backtest exposes to the graph: bar inputs,
   account/position state mirrored from the broker, and derived reads. *)
module Make
    (D : Rheo.Dsl.S)
    (B : sig
       val broker : Broker.t
     end) =
struct
  let open_ : float D.signal = D.input ()
  let high : float D.signal = D.input ()
  let low : float D.signal = D.input ()
  let close : float D.signal = D.input ()
  let lot_size = B.broker.lot_size

  module Account = struct
    let cash : float D.signal = D.input ()
    let realised_pnl : float D.signal = D.input ()
    let initial_balance : float D.signal = D.input ()
  end

  module Position = struct
    let open_lots : float D.signal = D.input ()
    let direction : float D.signal = D.input ()
    let entry_price = D.read (fun _ -> Broker.entry_price B.broker)

    let avg_entry_price =
      D.read (fun _ -> Broker.avg_entry_price B.broker)
    ;;
  end
end
