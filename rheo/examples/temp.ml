open Rheo

(* A small core DSL tour: external input, previous values, arithmetic,
   conditions, a recurrence, and the built-in tick counter. *)

let pp_float = function
  | None -> "None"
  | Some x -> Printf.sprintf "%.2f" x
;;

let pp_bool = function
  | None -> "None"
  | Some b -> if b then "true" else "false"
;;

module M = Dsl.Make ()
open M

let price = input ()
let previous = pre price
let change = Ops.(price - previous)
let rising = Ops.(change > !0.)

let total =
  recurrence (fun previous_total ->
    cond
      (is_pending previous_total)
      price
      Ops.(previous_total + price))
;;

module Runtime = (val compile ())

let prices = [| 5.; 6.; 7.; 6.; 8.; 10. |]

let () =
  Printf.printf
    "%-4s %-6s %-9s %-7s %-7s %-7s %s\n"
    "bar"
    "price"
    "previous"
    "change"
    "rising"
    "total"
    "tick";
  Array.iteri
    (fun i p ->
       Runtime.step [ Set (price, p) ];
       Printf.printf
         "%-4d %-6.2f %-9s %-7s %-7s %-7s %s\n"
         i
         p
         (pp_float (Runtime.value previous))
         (pp_float (Runtime.value change))
         (pp_bool (Runtime.value rising))
         (pp_float (Runtime.value total))
         (pp_float (Runtime.value tick)))
    prices
;;
