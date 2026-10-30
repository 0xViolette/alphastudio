(* Transaction costs and slippage for NSE equity derivatives.

   A model [t] maps each fill's context to a [result]: [slippage] is an
   adverse price adjustment in price units (buys fill higher, sells lower;
   the broker adds dir * slippage to the raw fill price) and [fee] is a
   cash amount debited from the account. Fill *detection* never depends on
   costs — only the executed price and the fee do.

   Rates are the NSE equity-derivatives schedule as of 2026-10-22:
     - NSE futures transaction charge  0.00183% each side (IPFT included)
     - NSE options transaction charge  0.03553% of premium each side
     - SEBI turnover fee               0.0001% each side
     - STT futures                     0.05% sell side
     - STT options                     0.15% of premium sell side
     - stamp duty futures              0.002% buy side
     - stamp duty options              0.003% of premium buy side
     - GST                             18% on brokerage + exchange + SEBI

   [Synthetic_futures] is a cost-only model of a two-leg synthetic future:
   buy call + sell put for a long, sell call + buy put for a short. It does
   not model option premium cash flows, expiry, or exercise; positions are
   assumed to be squared off before expiry. *)

type fill_ctx = {
  side : Order.side;
  kind : float Order.kind;
  price : float; (* raw fill price, before slippage *)
  lots : float;
  lot_size : float;
  bar : Codec.Ohlc.bar;
}

type result = { slippage : float; fee : float }
type preset = No_costs | Futures | Synthetic_futures | Simple

type params = {
  preset : preset;
  slip_ticks : float;
  slip_range_pct : float;
  fee_per_order : float;
  call_premium_pct : float;
  put_premium_pct : float;
  opt_slip_ticks : float;
  opt_slip_pct : float;
  tick_size_override : float option;
  opt_tick_size : float;
}

type t = { name : string; desc : string; apply : fill_ctx -> result }

let as_of = "2026-10-22"

(* Annual/statutory rates, in basis points of the relevant turnover. *)
type rates = {
  nse_futures_txn_bps : float;
  nse_options_txn_bps : float;
  sebi_bps : float;
  stt_futures_bps : float;
  stt_options_bps : float;
  stamp_futures_bps : float;
  stamp_options_bps : float;
  gst : float;
}

let rates =
  {
    nse_futures_txn_bps = 0.183;
    nse_options_txn_bps = 3.553;
    sebi_bps = 0.01;
    stt_futures_bps = 5.;
    stt_options_bps = 15.;
    stamp_futures_bps = 0.2;
    stamp_options_bps = 0.3;
    gst = 0.18;
  }

let default_params =
  {
    preset = Futures;
    slip_ticks = 1.;
    slip_range_pct = 1.;
    fee_per_order = 20.;
    call_premium_pct = 1.0;
    put_premium_pct = 0.8;
    opt_slip_ticks = 1.;
    opt_slip_pct = 0.5;
    tick_size_override = None;
    opt_tick_size = 0.05;
  }

let preset_name = function
  | No_costs -> "none"
  | Futures -> "futures"
  | Synthetic_futures -> "synthetic_futures"
  | Simple -> "simple"

let preset_of_string s =
  match String.lowercase_ascii (String.trim s) with
  | "none" | "off" | "zero" -> Some No_costs
  | "futures" | "india" | "india_futures" -> Some Futures
  | "synthetic" | "synthetic_futures" | "synth" | "options" ->
      Some Synthetic_futures
  | "simple" -> Some Simple
  | _ -> None

let presets =
  [
    (No_costs, "no costs, no slippage");
    (Futures, "NSE index futures: NSE txn, SEBI, STT sell, stamp buy, GST");
    ( Synthetic_futures,
      "synthetic futures via two option legs: 2x brokerage/txn/SEBI, STT on \
       the sell leg, stamp on the buy leg" );
    (Simple, "flat fee per order plus slippage only");
  ]

let validate p =
  let check name v =
    if (not (Float.is_finite v)) || v < 0. then
      invalid_arg
        (Printf.sprintf "costs: %s must be a finite number >= 0 (got %g)" name v)
  in
  check "slip_ticks" p.slip_ticks;
  check "slip_range_pct" p.slip_range_pct;
  check "fee_per_order" p.fee_per_order;
  check "call_premium_pct" p.call_premium_pct;
  check "put_premium_pct" p.put_premium_pct;
  check "opt_slip_ticks" p.opt_slip_ticks;
  check "opt_slip_pct" p.opt_slip_pct;
  (match p.tick_size_override with
  | None -> ()
  | Some v ->
      if (not (Float.is_finite v)) || v <= 0. then
        invalid_arg
          (Printf.sprintf
             "costs: tick_size must be a finite number > 0 (got %g)" v));
  if (not (Float.is_finite p.opt_tick_size)) || p.opt_tick_size <= 0. then
    invalid_arg
      (Printf.sprintf
         "costs: opt_tick_size must be a finite number > 0 (got %g)"
         p.opt_tick_size)

let none =
  {
    name = "none";
    desc = "no costs, no slippage";
    apply = (fun _ -> { slippage = 0.; fee = 0. });
  }

(* NSE price steps: 0.05 below 15,000; 0.10 from 15,000 to 30,000; 0.20 above. *)
let tick_size_for_price price =
  if price < 15_000. then 0.05 else if price < 30_000. then 0.10 else 0.20

let tick_size p ctx =
  Option.value p.tick_size_override ~default:(tick_size_for_price ctx.price)

(* ---- slippage parts: fill_ctx -> price units ---- *)

let slip_ticks n ~tick_size_ _ = n *. tick_size_

let slip_range_frac f ctx =
  let b = ctx.bar in
  f *. (b.Codec.Ohlc.high -. b.Codec.Ohlc.low)

let slip_sum parts ctx = List.fold_left (fun acc f -> acc +. f ctx) 0. parts

(* ---- fee parts: fill_ctx -> currency ---- *)

let turnover ctx = ctx.price *. ctx.lots *. ctx.lot_size
let fee_per_order amt _ = amt
let fee_bps turnover_bps turnover = turnover_bps *. 1e-4 *. turnover
let fee_bps_on_turnover bps ctx = fee_bps bps (turnover ctx)
let fee_sum parts ctx = List.fold_left (fun acc f -> acc +. f ctx) 0. parts

(* GST-style tax on a sub-sum of other fee parts *)
let tax_on rate parts ctx = rate *. fee_sum parts ctx

(* ---- composition ---- *)

let make ~name ~desc ~slip ~fee () =
  let open Order in
  {
    name;
    desc;
    apply =
      (fun ctx ->
        (* market and stop orders cross the spread; limit fills rest at
            their price and never slip *)
        let slippage =
          match ctx.kind with
          | Market | Stop _ -> Float.max 0. (slip ctx)
          | Limit _ -> 0.
        in
        let dir = Float.of_int (sign_of_side ctx.side) in
        let exec_price = ctx.price +. (dir *. slippage) in
        (* turnover-based fees are charged on the executed price *)
        { slippage; fee = Float.max 0. (fee { ctx with price = exec_price }) });
  }

let futures p =
  let brokerage = fee_per_order p.fee_per_order in
  let txn = fee_bps_on_turnover rates.nse_futures_txn_bps in
  let sebi = fee_bps_on_turnover rates.sebi_bps in
  let stt ctx =
    match ctx.side with
    | Order.Buy -> 0.
    | Order.Sell -> fee_bps rates.stt_futures_bps (turnover ctx)
  in
  let stamp ctx =
    match ctx.side with
    | Order.Buy -> fee_bps rates.stamp_futures_bps (turnover ctx)
    | Order.Sell -> 0.
  in
  let gst = tax_on rates.gst [ brokerage; txn; sebi ] in
  make ~name:"futures"
    ~desc:
      (Printf.sprintf
         "futures (NSE, rates as of %s): slip %.2g tick + %g%% bar range \
          (mkt/stop); fee flat %g + txn 0.183bps + sebi 0.01bps + stt \
          5bps(sell) + stamp 0.2bps(buy) + 18%% GST on (flat+txn+sebi)"
         as_of p.slip_ticks p.slip_range_pct p.fee_per_order)
    ~slip:(fun ctx ->
      slip_sum
        [
          slip_ticks p.slip_ticks ~tick_size_:(tick_size p ctx);
          slip_range_frac (p.slip_range_pct /. 100.);
        ]
        ctx)
    ~fee:(fee_sum [ brokerage; txn; sebi; stt; stamp; gst ])
    ()

let simple p =
  make ~name:"simple"
    ~desc:
      (Printf.sprintf "simple: slip %.2g tick (mkt/stop); fee flat %g"
         p.slip_ticks p.fee_per_order)
    ~slip:(fun ctx -> slip_ticks p.slip_ticks ~tick_size_:(tick_size p ctx) ctx)
    ~fee:(fee_per_order p.fee_per_order)
    ()

let synthetic p =
  let call_premium ctx = p.call_premium_pct /. 100. *. ctx.price in
  let put_premium ctx = p.put_premium_pct /. 100. *. ctx.price in
  let buy_premium ctx =
    match ctx.side with
    | Order.Buy -> call_premium ctx
    | Order.Sell -> put_premium ctx
  in
  let sell_premium ctx =
    match ctx.side with
    | Order.Buy -> put_premium ctx
    | Order.Sell -> call_premium ctx
  in
  let buy_turnover ctx = buy_premium ctx *. ctx.lots *. ctx.lot_size in
  let sell_turnover ctx = sell_premium ctx *. ctx.lots *. ctx.lot_size in
  let total_turnover ctx = buy_turnover ctx +. sell_turnover ctx in
  let brokerage _ = 2. *. p.fee_per_order in
  let txn ctx = fee_bps rates.nse_options_txn_bps (total_turnover ctx) in
  let sebi ctx = fee_bps rates.sebi_bps (total_turnover ctx) in
  let stt ctx = fee_bps rates.stt_options_bps (sell_turnover ctx) in
  let stamp ctx = fee_bps rates.stamp_options_bps (buy_turnover ctx) in
  let gst = tax_on rates.gst [ brokerage; txn; sebi ] in
  let leg_slip premium ctx =
    (p.opt_slip_ticks *. p.opt_tick_size)
    +. (p.opt_slip_pct /. 100. *. premium ctx)
  in
  make ~name:"synthetic_futures"
    ~desc:
      (Printf.sprintf
         "synthetic futures (options, rates as of %s): call premium %g%%, put \
          premium %g%%; slip per leg %.2g x %.2g tick + %g%% premium \
          (mkt/stop); fee 2 x flat %g + txn 3.553bps + sebi 0.01bps on both \
          legs + stt 15bps(sell leg) + stamp 0.3bps(buy leg) + 18%% GST on \
          (brokerage+txn+sebi)"
         as_of p.call_premium_pct p.put_premium_pct p.opt_slip_ticks
         p.opt_tick_size p.opt_slip_pct p.fee_per_order)
    ~slip:(fun ctx -> leg_slip call_premium ctx +. leg_slip put_premium ctx)
    ~fee:(fee_sum [ brokerage; txn; sebi; stt; stamp; gst ])
    ()

let of_params p =
  validate p;
  match p.preset with
  | No_costs -> none
  | Futures -> futures p
  | Synthetic_futures -> synthetic p
  | Simple -> simple p

type knob = {
  key : string;
  label : string;
  unit : string;
  default : float;
  min : float;
  applies_to : string list;
}

let knob ?(unit = "") ?(min = 0.) key label default applies_to =
  { key; label; unit; default; min; applies_to }

let knobs =
  [
    knob "slip_ticks" "Slippage ticks" default_params.slip_ticks
      [ "futures"; "simple" ];
    knob "slip_range_pct" "Slippage % of bar range"
      default_params.slip_range_pct [ "futures" ];
    knob "fee_per_order" "Flat fee per order" default_params.fee_per_order
      [ "futures"; "synthetic_futures"; "simple" ];
    knob "call_premium_pct" "Call premium % of underlying"
      default_params.call_premium_pct [ "synthetic_futures" ];
    knob "put_premium_pct" "Put premium % of underlying"
      default_params.put_premium_pct [ "synthetic_futures" ];
    knob "opt_slip_ticks" "Option slippage ticks per leg"
      default_params.opt_slip_ticks [ "synthetic_futures" ];
    knob "opt_slip_pct" "Option slippage % of premium"
      default_params.opt_slip_pct [ "synthetic_futures" ];
  ]
