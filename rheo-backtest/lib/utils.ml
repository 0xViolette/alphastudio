let die fmt =
  Printf.ksprintf
    (fun msg ->
       prerr_endline ("error: " ^ msg);
       exit 1)
    fmt
;;

let refuse_tty () =
  if In_channel.isatty In_channel.stdin
  then
    die
      "refusing to read binary from terminal\n\
       hint: redirect from a file (< ohlc.bin) or pipe from a producer\n"
;;

let read_exactly ic buf =
  let length = Bytes.length buf in
  let rec read_loop offset =
    if offset = length
    then `Ok
    else (
      match In_channel.input ic buf offset (length - offset) with
      | 0 -> if offset = 0 then `Eof else `Truncated (offset, length)
      | bytes_read -> read_loop (offset + bytes_read))
  in
  read_loop 0
;;
