type t =
  { ctx : Context.t
  ; mutable compiled : bool
  ; id : int
  }

let fresh_token =
  let c = ref 0 in
  fun () ->
    let token = !c in
    incr c;
    token
;;

let create () =
  { ctx = Context.create (); compiled = false; id = fresh_token () }
;;

open Language

let make g (def : 'a def) =
  if not g.compiled
  then Context.make g.ctx g.id def
  else failwith "Cannot mess with a compiled graph"
;;

let undefined g = make g Undefined Any
let const g v = make g (Const v) Float
let pure g v = make g (Pure (Some v)) Any
let input g = make g Input Float
let ginput g = make g Input Any

(* constant folding: an operation on constants is itself a constant *)
let map g f a = make g (Map (f, a)) Float
let map2 g f a b = make g (Map2 (f, a, b)) Float
let gmap g f a = make g (Gmap (f, a)) Any
let gmap2 g f a b = make g (Gmap2 (f, a, b)) Any
let pre g a = make g (Pre a) a.kind

let recurrence g body =
  let knot = make g Knot Float in
  let result = body knot in
  make g (Rec (knot, result)) Float
;;
