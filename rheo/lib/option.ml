include Stdlib.Option

let map2 f o1 o2 =
  match o1, o2 with
  | Some x, Some y -> Some (f x y)
  | _ -> None
;;
