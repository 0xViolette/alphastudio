(* Transaction costs and slippage, composed from small parts.

   A model [t] maps each fill's context to a [result]: [slippage] is an
   adverse price adjustment in price units (buys fill higher, sells lower;
   the broker adds dir * slippage to the raw fill price) and [fee] is a
   cash amount debited from the account. Fill *detection* never depends on
   costs — only the executed price and the fee do.

   Toggle via environment (real env wins; otherwise a [.env] file in the
   working directory is read):
     STRATEGY_COSTS = india (default) | simple | none
     STRATEGY_COSTS_SLIP_TICKS      override slippage ticks (market/stop)
     STRATEGY_COSTS_SLIP_RANGE_PCT  override % of bar range added to slip
     STRATEGY_COSTS_FEE_PER_ORDER   override flat fee per fill *)

type fill_ctx =
  { side : Order.side
  ; kind : float Order.kind
  ; price : float (* raw fill price, before slippage *)
  ; lots : float
  ; lot_size : float
  ; bar : Codec.Ohlc.bar
  }

type result =
  { slippage : float
  ; fee : float
  }

type t =
  { name : string
  ; desc : string
  ; apply : fill_ctx -> result
  }

let none =
  { name = "none"
  ; desc = "no costs, no slippage"
  ; apply = (fun _ -> { slippage = 0.; fee = 0. })
  }
;;

(* ---- slippage parts: fill_ctx -> price units ---- *)

let slip_ticks n ~tick_size _ = n *. tick_size
let slip_pct_of_price p ctx = p *. ctx.price

let slip_range_frac f ctx =
  let b = ctx.bar in
  f *. (b.Codec.Ohlc.high -. b.Codec.Ohlc.low)
;;

let slip_sum parts ctx =
  List.fold_left (fun acc f -> acc +. f ctx) 0. parts
;;

let slip_max a b ctx = Float.max (a ctx) (b ctx)

(* ---- fee parts: fill_ctx -> currency ---- *)

let turnover ctx = ctx.price *. ctx.lots *. ctx.lot_size
let fee_per_order amt _ = amt
let fee_per_lot amt ctx = amt *. ctx.lots
let fee_bps bps ctx = bps *. 1e-4 *. turnover ctx

let fee_bps_side ~buy ~sell ctx =
  fee_bps
    (match ctx.side with
     | Order.Buy -> buy
     | Order.Sell -> sell)
    ctx
;;

let fee_sum parts ctx =
  List.fold_left (fun acc f -> acc +. f ctx) 0. parts
;;

(* GST-style tax on a sub-sum of other fee parts *)
let tax_on rate parts ctx = rate *. fee_sum parts ctx

(* ---- composition ---- *)

let make ~name ~desc ~slip ~fee () =
  let open Order in
  { name
  ; desc
  ; apply =
      (fun ctx ->
        (* market and stop orders cross the spread; limit fills rest at
            their price and never slip *)
        let slippage =
          match ctx.kind with
          | Market | Stop _ -> Float.max 0. (slip ctx)
          | Limit _ -> 0.
        in
        let dir =
          match ctx.side with
          | Buy -> 1.
          | Sell -> -1.
        in
        let exec_price = ctx.price +. (dir *. slippage) in
        (* turnover-based fees are charged on the executed price *)
        { slippage
        ; fee = Float.max 0. (fee { ctx with price = exec_price })
        })
  }
;;

(* ---- presets ---- *)

let tick_size = 0.05

let india ~slip_ticks_n ~slip_range_pct ~fee_per_order_amt =
  let brokerage = fee_per_order fee_per_order_amt in
  let txn = fee_bps 0.173 in
  (* NSE futures transaction charges *)
  let sebi = fee_bps 0.01 in
  (* Rs 10 per crore *)
  let stt = fee_bps_side ~buy:0. ~sell:2. in
  (* 0.02%, sell side only *)
  let stamp = fee_bps_side ~buy:0.2 ~sell:0. in
  (* 0.002%, buy side only *)
  let gst = tax_on 0.18 [ brokerage; txn; sebi ] in
  make
    ~name:"india"
    ~desc:
      (Printf.sprintf
         "india: slip %.2g tick + %g%% bar range (mkt/stop); fee \
          flat %g + txn 0.173bps + sebi 0.01bps + stt 2bps(sell) + \
          stamp 0.2bps(buy) + 18%% GST on (flat+txn+sebi)"
         slip_ticks_n
         slip_range_pct
         fee_per_order_amt)
    ~slip:
      (slip_sum
         [ slip_ticks slip_ticks_n ~tick_size
         ; slip_range_frac (slip_range_pct /. 100.)
         ])
    ~fee:(fee_sum [ brokerage; txn; sebi; stt; stamp; gst ])
    ()
;;

let simple ~slip_ticks_n ~fee_per_order_amt =
  make
    ~name:"simple"
    ~desc:
      (Printf.sprintf
         "simple: slip %.2g tick (mkt/stop); fee flat %g"
         slip_ticks_n
         fee_per_order_amt)
    ~slip:(slip_ticks slip_ticks_n ~tick_size)
    ~fee:(fee_per_order fee_per_order_amt)
    ()
;;

(* ---- environment toggle ---- *)

(* A [.env] file in the working directory supplies variables the real
   environment does not set; real environment variables win. Lines are
   KEY=value, optional [export ] prefix, optional quotes, [#] comments. *)
let dotenv =
  lazy
    (let tbl = Hashtbl.create 8 in
     (match open_in ".env" with
      | ic ->
        (try
           while true do
             let line = String.trim (input_line ic) in
             (match line with
              | "" -> ()
              | _ when line.[0] = '#' -> ()
              | _ ->
                let line =
                  if String.starts_with ~prefix:"export " line
                  then String.trim (String.sub line 7 (String.length line - 7))
                  else line
                in
                (match String.index_opt line '=' with
                 | None -> ()
                 | Some i ->
                   let key = String.trim (String.sub line 0 i) in
                   let v =
                     String.trim
                       (String.sub line (i + 1) (String.length line - i - 1))
                   in
                   let n = String.length v in
                   let v =
                     if n >= 2
                        && ((v.[0] = '"' && v.[n - 1] = '"')
                            || (v.[0] = '\'' && v.[n - 1] = '\''))
                     then String.sub v 1 (n - 2)
                     else v
                   in
                   if key <> "" then Hashtbl.replace tbl key v))
           done
         with
         | End_of_file -> close_in_noerr ic)
      | exception Sys_error _ -> ());
     tbl)
;;

let getenv name =
  match Sys.getenv_opt name with
  | Some _ as v -> v
  | None -> Hashtbl.find_opt (Lazy.force dotenv) name
;;

let getenv_num name =
  match getenv name with
  | None -> None
  | Some s ->
    (match float_of_string_opt (String.trim s) with
     | Some _ as v -> v
     | None ->
       Printf.eprintf
         "costs: ignoring invalid %s=%S (want a number)\n%!"
         name
         s;
       None)
;;

let from_env () =
  let preset =
    match getenv "STRATEGY_COSTS" with
    | None -> "india"
    | Some s -> String.lowercase_ascii (String.trim s)
  in
  let source =
    if Sys.getenv_opt "STRATEGY_COSTS" <> None
    then "env"
    else if Hashtbl.mem (Lazy.force dotenv) "STRATEGY_COSTS"
    then ".env"
    else "default"
  in
  let slip_ticks =
    Option.value (getenv_num "STRATEGY_COSTS_SLIP_TICKS") ~default:1.
  in
  let slip_range_pct =
    Option.value
      (getenv_num "STRATEGY_COSTS_SLIP_RANGE_PCT")
      ~default:1.
  in
  let per_order =
    Option.value
      (getenv_num "STRATEGY_COSTS_FEE_PER_ORDER")
      ~default:20.
  in
  let t =
    match preset with
    | "none" | "off" | "zero" -> none
    | "india" | "india_futures" ->
      india
        ~slip_ticks_n:slip_ticks
        ~slip_range_pct
        ~fee_per_order_amt:per_order
    | "simple" ->
      simple ~slip_ticks_n:slip_ticks ~fee_per_order_amt:per_order
    | other ->
      Printf.eprintf
        "costs: unknown STRATEGY_COSTS=%S (want india|simple|none); \
         using none\n\
         %!"
        other;
      none
  in
  Printf.eprintf "costs: %s [%s]\n%!" t.desc source;
  t
;;
