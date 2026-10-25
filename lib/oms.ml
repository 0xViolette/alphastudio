open Order

let side_of = function
  | Enter_long _ | Exit_short -> Buy
  | Enter_short _ | Exit_long -> Sell
;;

(* net is directional (+/-) *)
let lots_for_action action ~net =
  match action with
  | Enter_long target -> if net > 0. then target else target -. net
  | Enter_short target -> if net < 0. then target else target +. net
  | Exit_long -> if net > 0. then net else 0.
  | Exit_short -> if net < 0. then -.net else 0.
;;

let place_orders b requests =
  let net = Broker.signed_net_lots b in
  List.filter_map
    (fun request ->
       let q = lots_for_action request.action ~net in
       if q > 0.
       then (
         Broker.place
           b
           ~id:request.id
           (side_of request.action)
           request.kind
           q;
         Some request)
       else None)
    requests
;;

let sync_resting_orders b resting_requests =
  let net = Broker.signed_net_lots b in
  List.iter
    (fun request ->
       let q = lots_for_action request.action ~net in
       if q > 0.
       then Broker.modify b request.id ~qty:q ()
       else Broker.cancel b request.id)
    resting_requests
;;

let fill_resting_orders broker resting =
  let rec go fills =
    match Broker.next_fill broker with
    | None -> List.rev fills
    | Some f ->
      sync_resting_orders broker resting;
      go (f :: fills)
  in
  go []
;;
