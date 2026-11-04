(* The trading environment a backtest exposes to the graph: bar inputs,
   account/position inputs, and derived signals. *)
module Make (D : Rheo.Dsl.S) = struct
  let open_ : float D.signal = D.input ()
  let high : float D.signal = D.input ()
  let low : float D.signal = D.input ()
  let close : float D.signal = D.input ()
  let lot_size = D.input ()

  module Account = struct
    let cash : float D.signal = D.input ()
    let realised_pnl : float D.signal = D.input ()
    let initial_balance : float D.signal = D.input ()
  end

  module Position = struct
    open D
    open D.Ops

    let open_lots = input ()
    let direction = input ()
    let is_long = direction > !0.
    let is_short = direction < !0.
    let is_flat = direction = !0.
    let entered_long = is_long && pre direction <= !0.
    let entered_short = is_short && pre direction >= !0.
    let entered_trade = entered_long || entered_short
    let exited_trade = is_flat && pre direction <> !0.
    let entry_price = input ()
    let avg_entry_price = input ()
  end
end
