(* everything that happened on one bar *)
type t = {
  bar : Codec.Ohlc.bar;
  fills : Order.fill list;
  placed : Order.request list (* new this bar; continuing orders omitted *);
  probes : (string * float option) list (* read after the graph stepped *);
}

let num x = Printf.sprintf "%.10g" x
let cell = function Some x -> num x | None -> ""

(* timestamps arrive as unix seconds reading the IST wall clock; recover the
   source CSV's Date/Time by shifting to IST and taking the UTC breakdown *)
let date_time ts =
  let tm = Unix.gmtime (Int64.to_float ts +. 19800.) in
  let date =
    Printf.sprintf "%02d/%02d/%02d" (tm.tm_mon + 1) tm.tm_mday
      (tm.tm_year mod 100)
  in
  let h12 = tm.tm_hour mod 12 in
  let h12 = if h12 = 0 then 12 else h12 in
  let ampm = if tm.tm_hour < 12 then "AM" else "PM" in
  (date, Printf.sprintf "%02d:%02d %s" h12 tm.tm_min ampm)

let describe_placed (r : Order.request) =
  match r.kind with
  | Order.Market -> r.id ^ " placed @ mkt"
  | Order.Limit p | Order.Stop p -> Printf.sprintf "%s placed @ %s" r.id (num p)

(* fills first, then new placements, same order as your old event column *)
let events (r : t) =
  let filled = List.map (fun (f : Order.fill) -> f.id ^ " filled") r.fills in
  String.concat " | " (filled @ List.map describe_placed r.placed)

let csv_header probe_names =
  String.concat ","
    ([ "Date"; "Time"; "Open"; "High"; "Low"; "Close" ]
    @ probe_names
    @ [ "event"; "fill_id"; "fill_side"; "fill_price"; "fill_qty"; "fill_cost" ]
    )

let csv_row (r : t) =
  let { Codec.Ohlc.timestamp; open_; high; low; close; _ } = r.bar in
  let date, time = date_time timestamp in
  let pipe f = String.concat "|" (List.map f r.fills) in
  String.concat ","
    ([ date; time; num open_; num high; num low; num close ]
    @ List.map (fun (_, v) -> cell v) r.probes
    @ [
        events r;
        pipe (fun (f : Order.fill) -> f.id);
        pipe (fun (f : Order.fill) ->
          Printf.sprintf "%+d" (Order.sign_of_side f.side));
        pipe (fun (f : Order.fill) -> num f.price);
        pipe (fun (f : Order.fill) -> num f.lots);
        pipe (fun (f : Order.fill) -> num f.fees);
      ])
