open Ast

(* Per-node machine op; signal state lives in flat arrays indexed by signal
   id (ids are dense per kind). Float signals live in unboxed float arrays
   with [nan] as the pending marker; effect signals live in [Obj.t] arrays
   holding the node's ['a option] erased to [Obj.t] — representation-safe,
   as an option is always immediate [None] or a [Some] block. *)
type src =
  | F of int (* float slot *)
  | E of int (* effect slot *)

let src_of : type a. a signal -> src =
  fun s ->
  match s.kind with
  | Float -> F s.id
  | Any -> E s.id
;;

type instr =
  | IPrev of
      int * int (* float_curs.(d) <- float_prevs.(s): Pre, Knot *)
  | IPrevE of int * int (* effect_curs.(d) <- effect_curs.(s): Pre *)
  | ICopy of int * int (* float_curs.(d) <- float_curs.(s): Rec *)
  | IUnbox of
      int * int (* float_curs.(d) <- effect_curs.(s); None -> nan *)
  | IMap of int * (float -> float) * int
  | IMap2 of int * (float -> float -> float) * int * int
  | IGMap of int * (Obj.t -> Obj.t) * src
  | IGmap2 of int * (Obj.t -> Obj.t -> Obj.t) * src * src

type t =
  { instrs : instr array
  ; mutable float_curs : float array
  ; mutable float_prevs : float array
  ; mutable effect_curs : Obj.t array
  ; mutable effect_prevs : Obj.t array
  ; inputs : src array
  ; graph_id : int
  }

let compile (g : Graph.t) =
  (* Array of all signals that are part of G *)
  let signals = Array.of_list g.ctx.registry in
  Array.stable_sort
    (fun (Packed l) (Packed r) -> compare l.rank r.rank)
    signals;
  (* srcs of all input signals; ids alone are ambiguous, being dense per
     kind, so inputs are tracked as kind-tagged slots *)
  let inputs =
    Array.fold_left
      (fun srcs (Packed s) ->
         match s.def with
         | Input -> src_of s :: srcs
         | _ -> srcs)
      []
      signals
    |> Array.of_list
  in
  (* wire each knot to the loop that owns it *)
  Array.iter
    (fun (Packed s) ->
       match s.def with
       | Rec (knot, _) -> knot.knot_target <- Some s
       | _ -> ())
    signals;
  (* a float-typed operand may still be an effect-kind node (e.g. one built
     by [read]/[gmap]); route it through a coercion slot that maps [None] to
     [nan]. One slot per source, coerced before its first consumer. *)
  let extra_floats = ref 0 in
  let coercions = Hashtbl.create 8 in
  let consts = ref [] in
  let pures = ref [] in
  let float_slot (s : float signal) =
    match s.kind with
    | Float -> s.id, []
    | Any ->
      (match Hashtbl.find_opt coercions s.id with
       | Some slot -> slot, []
       | None ->
         let slot = g.ctx.float_counter + !extra_floats in
         incr extra_floats;
         Hashtbl.add coercions s.id slot;
         slot, [ IUnbox (slot, s.id) ])
  in
  let instrs =
    List.rev
      (Array.fold_left
         (fun acc (Packed s) ->
            match s.def with
            | Undefined -> acc
            | Const v ->
              (* no instruction: both buffers agree, so swaps keep it constant *)
              consts := (s.id, v) :: !consts;
              acc
            | Input -> acc
            | Pre src ->
              (match s.kind with
               | Float -> IPrev (s.id, src.id) :: acc
               | Any -> IPrevE (s.id, src.id) :: acc)
            | Knot ->
              (match s.knot_target with
               | None ->
                 failwith
                   "compile: dangling knot (not created by loop)"
               | Some target -> IPrev (s.id, target.id) :: acc)
            | Rec (_, result) ->
              (match result.kind with
               | Float -> ICopy (s.id, result.id) :: acc
               | Any -> IUnbox (s.id, result.id) :: acc)
            | Map (f, src) ->
              let src, coerce = float_slot src in
              IMap (s.id, f, src) :: (coerce @ acc)
            | Map2 (f, a, b) ->
              let a, coerce_a = float_slot a in
              let b, coerce_b = float_slot b in
              IMap2 (s.id, f, a, b) :: (coerce_a @ coerce_b @ acc)
            | Pure v ->
              (* no instruction: both buffers agree, so swaps keep it constant *)
              pures := (s.id, Obj.repr v) :: !pures;
              acc
            | Gmap (f, src) ->
              IGMap
                (s.id, (fun o -> Obj.repr (f (Obj.obj o))), src_of src)
              :: acc
            | Gmap2 (f, a, b) ->
              IGmap2
                ( s.id
                , (fun oa ob ->
                    Obj.repr (f (Obj.obj oa) (Obj.obj ob)))
                , src_of a
                , src_of b )
              :: acc)
         []
         signals)
    |> Array.of_list
  in
  let float_size = g.ctx.float_counter + !extra_floats in
  let float_curs = Array.make float_size nan in
  let float_prevs = Array.make float_size nan in
  List.iter
    (fun (id, v) ->
       float_curs.(id) <- v;
       float_prevs.(id) <- v)
    !consts;
  let effect_curs = Array.make g.ctx.effect_counter (Obj.repr None) in
  let effect_prevs =
    Array.make g.ctx.effect_counter (Obj.repr None)
  in
  List.iter
    (fun (id, v) ->
       effect_curs.(id) <- v;
       effect_prevs.(id) <- v)
    !pures;
  { instrs
  ; float_curs
  ; float_prevs
  ; effect_curs
  ; effect_prevs
  ; inputs
  ; graph_id = g.id
  }
;;

let read_src r = function
  | F i ->
    let v = r.float_curs.(i) in
    Obj.repr (if Float.is_nan v then None else Some v)
  | E i -> r.effect_curs.(i)
;;

let exec r instr =
  match instr with
  | IPrev (d, s) -> r.float_curs.(d) <- r.float_prevs.(s)
  | IPrevE (d, s) -> r.effect_curs.(d) <- r.effect_prevs.(s)
  | ICopy (d, s) -> r.float_curs.(d) <- r.float_curs.(s)
  | IUnbox (d, s) ->
    r.float_curs.(d)
    <- (match (Obj.obj r.effect_curs.(s) : float option) with
        | Some v -> v
        | None -> nan)
  | IMap (d, f, a) -> r.float_curs.(d) <- f r.float_curs.(a)
  | IMap2 (d, f, a, b) ->
    r.float_curs.(d) <- f r.float_curs.(a) r.float_curs.(b)
  | IGMap (d, f, s) -> r.effect_curs.(d) <- f (read_src r s)
  | IGmap2 (d, f, a, b) ->
    r.effect_curs.(d) <- f (read_src r a) (read_src r b)
;;

type set = Set : 'a signal * 'a -> set

let step r setters =
  (* validate first: a failing step leaves the runtime state untouched *)
  List.iter
    (fun (Set (s, _)) ->
       if s.graph_id <> r.graph_id
       then failwith "step: signal belongs to a different graph";
       match s.def with
       | Input -> ()
       | _ -> failwith "step: cannot set non input node")
    setters;
  if
    Array.exists
      (fun src ->
         not
           (List.exists
              (fun (Set (s, _)) ->
                 (* no [src_of]/[=]: those allocate a variant per compare *)
                 match src, s.kind with
                 | F i, Float -> s.id = i
                 | E i, Any -> s.id = i
                 | _ -> false)
              setters))
      r.inputs
  then failwith "step: all inputs must be set";
  if Array.length r.inputs <> List.length setters
  then failwith "step: an input is set more than once"
  else (
    (* rotate buffers: every node's last-bar cur becomes its prev *)
    let fc = r.float_curs in
    let ec = r.effect_curs in
    r.float_curs <- r.float_prevs;
    r.float_prevs <- fc;
    r.effect_curs <- r.effect_prevs;
    r.effect_prevs <- ec;
    List.iter
      (fun (Set (s, v)) ->
         match s.kind with
         | Float -> r.float_curs.(s.id) <- v
         | Any -> r.effect_curs.(s.id) <- Obj.repr (Some v))
      setters;
    for i = 0 to Array.length r.instrs - 1 do
      exec r r.instrs.(i)
    done)
;;

let value : type a. t -> a signal -> a option =
  fun r s ->
  if s.graph_id <> r.graph_id
  then failwith "value: signal belongs to a different graph";
  match s.kind with
  | Any -> Obj.obj r.effect_curs.(s.id)
  | Float ->
    let v = r.float_curs.(s.id) in
    if Float.is_nan v then None else Some v
;;
