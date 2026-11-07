let cfg = RheoBacktest.Cli.parse Sys.argv

module S = RheoBacktest.Backtest.Make ()

let () =
  let open S in
  let open S.Ops in
  let echannel = 10 in
  let risk = 0.0035 in
  let leverage = 2.0 in
  let atrgunak = 2. in
  let ma = 65 in
  let atrbars = 20 in
  let le = Ta.highest echannel high + !0.05 in
  let se = Ta.lowest echannel low - !0.05 in
  let entered_trade = Position.entered_trade in
  let bars_in_trade = Ta.bars_since entered_trade in
  let no_profit_long = close <= Position.entry_price in
  let no_profit_short = close >= Position.entry_price in
  let atr1 = Ta.sma atrbars Ta.tr in
  let atr = Ta.ema 20 atr1 in
  let mov = Ta.ema ma close in
  let netequity =
    (Account.initial_balance + Account.realised_pnl) * !leverage
  in
  let qty1 = netequity * !risk / (!atrgunak * atr * lot_size) in
  let con1 = floor qty1 in
  let qty2 = netequity / (le * lot_size) in
  let con2 = floor qty2 in
  let qty3 = netequity / (se * lot_size) in
  let con3 = floor qty3 in
  let con4 = !1.0 in
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
  let trail_mult = 2.0 in
  let trail_n = 22 in
  let chand_l = Ta.highest trail_n high - (!trail_mult * atr) in
  let chand_s = Ta.lowest trail_n low + (!trail_mult * atr) in
  exit_long "2NL" (Stop lx2n) (Position.entry_price > se);
  exit_long "TRL" (Stop chand_l)
    (chand_l > Position.entry_price && chand_l < close);
  exit_short "2NS" (Stop sx2n) (Position.entry_price < le);
  exit_short "TRS" (Stop chand_s)
    (chand_s < Position.entry_price && chand_s > close);
  exit_short "LE cover" (Stop le) bullish;
  enter_long "LE" tradecon (Stop le) bullish;
  exit_long "SE cover" (Stop se) bearish;
  enter_short "SE" tradecon (Stop se) bearish;
  exit_long "TIME" Market (Position.is_long && ((bars_in_trade > !13. && no_profit_long) || (bars_in_trade > !39.)));
  exit_short "TIME" Market (Position.is_short && ((bars_in_trade > !13. && no_profit_short) || (bars_in_trade > !39.)));
  observe atr1 "atr1";
  observe atr "atr";
  observe mov "mov";
  observe tradecon "tradecon";
  observe atr_at_entry "atr_at_entry";
  observe lx2n "lx2n";
  observe sx2n "sx2n";
  observe le "le";
  observe se "se";
  observe chand_l "chand_l";
  observe chand_s "chand_s";
  S.run_csv_cli cfg
