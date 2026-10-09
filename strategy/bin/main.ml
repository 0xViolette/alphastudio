open Lib
module Papa = Ohlc.Make ()

let strat () =
  let open Papa in
  let open Papa.Ops in
  let echannel = 10 in
  let risk = 0.01 in
  let leverage = 2.0 in
  let atrgunak = 2. in
  let ma = 65 in
  let atrbars = 20 in
  let le = Indicator.highest echannel high + !0.05 in
  let se = Indicator.lowest echannel low - !0.05 in
  let entered_long =
    Position.direction > !0. && pre Position.direction <= !0.
  in
  let entered_short =
    Position.direction < !0. && pre Position.direction >= !0.
  in
  let entered_trade = entered_long || entered_short in
  let atr1 = Indicator.sma atrbars Indicator.tr in
  let atr = Indicator.ema 20 atr1 in
  let mov = Indicator.ema ma close in
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
  let finalcon = Indicator.min con1 (Indicator.min con2 con3) in
  let tradecon = Indicator.max finalcon con4 in
  let atr_at_entry =
    cond
      (Indicator.bars_since entered_trade <= !50.)
      (value_when entered_trade atr)
      (delay 50 atr)
  in
  let lx2n = Position.entry_price - (!atrgunak * atr_at_entry) in
  let sx2n = Position.entry_price + (!atrgunak * atr_at_entry) in
  let bullish = close > mov
  and bearish = close < mov in
  let stop_l =
    exit_long "2NL" (Stop lx2n) (Position.entry_price > se)
  in
  let stop_s =
    exit_short "2NS" (Stop sx2n) (Position.entry_price < le)
  in
  let cover_s = exit_short "LE cover" (Stop le) bullish in
  let enter_long = enter_long "LE" tradecon (Stop le) bullish in
  let cover_l = exit_long "SE cover" (Stop se) bearish in
  let enter_short = enter_short "SE" tradecon (Stop se) bearish in
  let orders =
    [ stop_l; stop_s; cover_s; enter_long; cover_l; enter_short ]
  in
  let probes =
    [ "atr1", atr1
    ; "atr", atr
    ; "mov", mov
    ; "tradecon", tradecon
    ; "atr_at_entry", atr_at_entry
    ; "lx2n", lx2n
    ; "sx2n", sx2n
    ]
  in
  orders, probes
;;

let () =
  let t0 = Sys.time () in
  let orders, probes = strat () in
  Printf.printf "%s\n" (Report.csv_header (List.map fst probes));
  Papa.backtest
    ~probes
    ~on_bar:(fun r -> Printf.printf "%s\n" (Report.csv_row r))
    orders;
  Printf.eprintf "Execution time: %.4f seconds\n" (Sys.time () -. t0)
;;
