module C = Codec.Ohlc

(* Bars are chronological, so the window is drop-while/skip + stop: bars
   before from_ts are skipped, the stream ends at till_ts (exclusive). *)
let stdin_bars ?(from_ts = None) ?(till_ts = None) () : C.bar Seq.t =
  Utils.refuse_tty ();
  let buf = Bytes.create C.message_size in
  let rec next () =
    match Utils.read_exactly In_channel.stdin buf with
    | `Ok ->
      let bar : C.bar = C.decode buf in
      let before_from =
        match from_ts with
        | Some ts -> Int64.compare bar.timestamp ts < 0
        | None -> false
      in
      let past_till =
        match till_ts with
        | Some ts -> Int64.compare bar.timestamp ts >= 0
        | None -> false
      in
      if past_till
      then Seq.Nil
      else if before_from
      then next ()
      else Seq.Cons (bar, next)
    | `Eof -> Seq.Nil
    | `Truncated (recieved, expected) ->
      Utils.die
        "truncated record: received %d bytes, expected %d"
        recieved
        expected
  in
  next
;;
