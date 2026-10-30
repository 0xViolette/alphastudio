type side = Buy | Sell

let sign_of_side = function Buy -> 1 | Sell -> -1

type 'a kind = Market | Limit of 'a | Stop of 'a

type fill = {
  id : string;
  side : side;
  price : float;
  lots : float;
  fees : float;
}

type order = {
  id : string;
  seq : int;
  side : side;
  kind : float kind;
  lots : float;
}

type request = { id : string; action : action; kind : float kind }

and action =
  | Enter_long of float
    (* be long [target]; covers a short first; no pyramiding *)
  | Enter_short of float
  | Exit_long (* flatten a long; no-op otherwise *)
  | Exit_short
