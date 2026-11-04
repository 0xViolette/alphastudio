module Make (D : Rheo.Dsl.S) : sig
  val open_ : float D.signal
  val high : float D.signal
  val low : float D.signal
  val close : float D.signal
  val lot_size : float D.signal

  module Account : sig
    val cash : float D.signal
    (** True cash balance: changes only with realised PnL and fees. Equity also
        includes unrealised PnL. *)

    val realised_pnl : float D.signal
    val initial_balance : float D.signal
  end

  module Position : sig
    val open_lots : float D.signal
    val direction : float D.signal
    val is_long : bool D.signal
    val is_short : bool D.signal
    val is_flat : bool D.signal
    val entered_long : bool D.signal
    val entered_short : bool D.signal
    val entered_trade : bool D.signal
    val exited_trade : bool D.signal
    val entry_price : float D.signal
    val avg_entry_price : float D.signal
  end
end
