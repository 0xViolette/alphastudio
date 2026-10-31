let cfg = RheoBacktest.Costs_cli.parse Sys.argv

module S =
  RheoBacktest.Backtest.Make
    (struct
      let lot_size = cfg.lot_size
      let initial_balance = cfg.initial_balance
      let costs = cfg.costs
    end)
    ()

module Ta = RheoTa.Make (S.D) (S.Env)

let strat () =
  let open S in
  let open S.Ops in
  let echannel = 10 in
  let risk = 0.01 in
  let leverage = 2.0 in
  let atrgunak = 2. in
  let ma = 65 in
  let atrbars = 20 in
  let le = Ta.highest echannel high + !0.05 in
  let se = Ta.lowest echannel low - !0.05 in
  let entered_trade = Position.entered_trade in
  let atr1 = Ta.sma atrbars Ta.tr in
  let atr = Ta.ema 20 atr1 in
  let mov = Ta.ema ma close in
  let netequity =
    (Account.initial_balance + Account.realised_pnl) * !leverage
  in
  let qty1 = netequity * !risk / (!atrgunak * atr * !lot_size) in
  let con1 = floor qty1 in
  let qty2 = netequity / (le * !lot_size) in
  let con2 = floor qty2 in
  let qty3 = netequity / (se * !lot_size) in
  let con3 = floor qty3 in
  let con4 = !2.0 in
  let finalcon = min con1 (min con2 con3) in
  let tradecon = max finalcon con4 in
  (* let tradecon = Indicator.min !1. (Indicator.min finalcon con4) in *)
  let atr_at_entry =
    cond
      (Ta.bars_since entered_trade <= !50.)
      (value_when entered_trade atr)
      (delay 50 atr)
  in
  let lx2n = Position.entry_price - (!atrgunak * atr_at_entry) in
  let sx2n = Position.entry_price + (!atrgunak * atr_at_entry) in
  let bullish = close > mov and bearish = close < mov in
  let stop_l = exit_long "2NL" (Stop lx2n) (Position.entry_price > se) in
  let stop_s = exit_short "2NS" (Stop sx2n) (Position.entry_price < le) in
  let cover_s = exit_short "LE cover" (Stop le) bullish in
  let enter_long = enter_long "LE" tradecon (Stop le) bullish in
  let cover_l = exit_long "SE cover" (Stop se) bearish in
  let enter_short = enter_short "SE" tradecon (Stop se) bearish in
  let trail_mult = 2.0 in
  let trail_n = 22 in
  let chand_l = Ta.highest trail_n high - (!trail_mult * atr) in
  let chand_s = Ta.lowest trail_n low + (!trail_mult * atr) in
  (* 1 *)
  let trail_l =
    exit_long "TRL" (Stop chand_l)
      (chand_l > Position.entry_price && chand_l < close)
  in
  let trail_s =
    exit_short "TRS" (Stop chand_s)
      (chand_s < Position.entry_price && chand_s > close)
  in
  (* 2 *)
  (* let trail_l = *)
  (*   exit_long "TRL" Market (chand_l > Position.entry_price) *)
  (* in *)
  (* let trail_s = *)
  (*   exit_short "TRS" Market (chand_s < Position.entry_price) *)
  (* in *)
  let orders =
    [
      stop_l;
      trail_l;
      stop_s;
      trail_s;
      cover_s;
      enter_long;
      cover_l;
      enter_short;
    ]
  in
  let probes =
    [
      ("atr1", atr1);
      ("atr", atr);
      ("mov", mov);
      ("tradecon", tradecon);
      ("atr_at_entry", atr_at_entry);
      ("lx2n", lx2n);
      ("sx2n", sx2n);
      ("le", le);
      ("se", se);
      ("chand_l", chand_l);
      ("chand_s", chand_s);
    ]
  in
  (orders, probes)

let () =
  let t0 = Sys.time () in
  let orders, probes = strat () in
  Printf.printf "%s\n" (RheoBacktest.Report.csv_header (List.map fst probes));
  S.backtest ~from_ts:cfg.from_ts ~till_ts:cfg.till_ts ~probes
    ~on_bar:(fun r -> Printf.printf "%s\n" (RheoBacktest.Report.csv_row r))
    orders;
  Printf.eprintf "Execution time: %.4f seconds\n" (Sys.time () -. t0)
