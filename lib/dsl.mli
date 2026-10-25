module type S = Dsl_intf.S

module Make : functor () -> S
