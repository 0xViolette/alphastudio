open Language

type t =
  { mutable float_counter : int
  ; mutable effect_counter : int
  ; mutable registry : any_signal list
  }

let create () =
  { float_counter = 0
  ; effect_counter = 0
  ; registry = [] (*cache = Hashtbl.create 16*)
  }
;;

(* a node may only reference signals of its own graph; checked before the
   cache so a foreign reference cannot hash-cons into a local signal *)
let check_operands : type a. int -> a def -> unit =
  fun graph_id def ->
  let check s =
    if s.graph_id <> graph_id
    then failwith "make: operand belongs to a different graph"
  in
  match def with
  | Undefined | Const _ | Pure _ | Input | Knot -> ()
  | Pre src -> check src
  | Rec (knot, result) ->
    check knot;
    check result
  | Map (_, src) -> check src
  | Map2 (_, a, b) ->
    check a;
    check b
  | Gmap (_, src) -> check src
  | Gmap2 (_, a, b) ->
    check a;
    check b
;;

let rec rank_of : type a. a def -> int = function
  | Undefined | Const _ | Pure _ | Input | Knot -> 0
  | Pre s -> 1 + s.rank
  | Map (_, s) -> 1 + s.rank
  | Map2 (_, l, r) -> 1 + Int.max l.rank r.rank
  | Gmap (_, s) -> 1 + s.rank
  | Gmap2 (_, l, r) -> 1 + Int.max l.rank r.rank
  | Rec (_, result) -> 1 + result.rank
;;

let make t graph_id =
  fun (type a)
    (def : a def)
    (kind : a Language.signal_kind)
    : a signal ->
  check_operands graph_id def;
  let fresh_signal () =
    let new_id =
      match kind with
      | Any ->
        t.effect_counter <- t.effect_counter + 1;
        t.effect_counter - 1
      | Float ->
        t.float_counter <- t.float_counter + 1;
        t.float_counter - 1
    in
    let signal =
      { id = new_id
      ; kind
      ; graph_id
      ; rank = rank_of def
      ; def
      ; knot_target = None
      ; pre_child = None
      }
    in
    t.registry <- Packed signal :: t.registry;
    signal
  in
  match def with
  | Pre src ->
    (match src.pre_child with
     | Some signal -> signal
     | None ->
       let signal = fresh_signal () in
       src.pre_child <- Some signal;
       signal)
  | _ -> fresh_signal ()
;;
