module DList = struct
  type 'a node =
    { value : 'a
    ; mutable prev : 'a node option
    ; mutable next : 'a node option
    }

  type 'a t =
    { mutable head : 'a node option
    ; mutable tail : 'a node option
    }

  let create () = { head = None; tail = None }

  let clear t =
    t.head <- None;
    t.tail <- None
  ;;

  let append t x =
    let new_node = { value = x; prev = None; next = None } in
    match t.tail with
    | Some tail ->
      new_node.prev <- Some tail;
      tail.next <- Some new_node;
      t.tail <- Some new_node
    | None ->
      t.head <- Some new_node;
      t.tail <- Some new_node
  ;;

  let remove t node =
    (match node.prev with
     | Some prev -> prev.next <- node.next
     | None -> t.head <- node.next);
    (match node.next with
     | Some next -> next.prev <- node.prev
     | None -> t.tail <- node.prev);
    node.prev <- None;
    node.next <- None
  ;;

  let head t = t.head
  let tail t = t.tail
  let value node = node.value
end
