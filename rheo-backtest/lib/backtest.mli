module Make () : sig
  include Rheo.Dsl.S

  val open_ : float signal
  val high : float signal
  val low : float signal
  val close : float signal
  val lot_size : float signal

  module Account : sig
    val cash : float signal
    (** True cash balance: changes only with realised PnL and fees. Equity also
        includes unrealised PnL. *)

    val realised_pnl : float signal
    val initial_balance : float signal
  end

  module Position : sig
    val open_lots : float signal
    val direction : float signal
    val is_long : bool signal
    val is_short : bool signal
    val is_flat : bool signal
    val entered_long : bool signal
    val entered_short : bool signal
    val entered_trade : bool signal
    val exited_trade : bool signal
    val entry_price : float signal
    val avg_entry_price : float signal
  end

  module Ta : sig
    val sum : int -> float signal -> float signal
    val sma : int -> float signal -> float signal
    val ema : int -> float signal -> float signal
    val crossover : float signal -> float signal -> bool signal
    val crossunder : float signal -> float signal -> bool signal
    val highest : int -> float signal -> float signal
    val lowest : int -> float signal -> float signal
    val bars_since : bool signal -> float signal
    val tr : float signal
    val atr : int -> float signal
  end

  val observe : float signal -> string -> unit

  val enter_long :
    string -> float signal -> float signal Order.kind -> bool signal -> unit

  val enter_short :
    string -> float signal -> float signal Order.kind -> bool signal -> unit

  val exit_long : string -> float signal Order.kind -> bool signal -> unit
  val exit_short : string -> float signal Order.kind -> bool signal -> unit

  val run :
    config:Config.t -> ?on_bar:(Bar_report.t -> unit) -> Bar.t Seq.t -> unit
  (** [run ~config bars] executes orders from the previous bar, then evaluates
      the graph with current-bar and post-fill portfolio inputs. Requests absent
      from a bar's new order set are cancelled implicitly.
      @raise Invalid_argument if this Backtest instance has already run. *)

  val run_csv_cli : Config.t -> unit
end
