#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d "$ROOT/_opam" ]]; then
    opam switch create . ocaml-base-compiler.5.5.2 --no-install
fi

eval "$(opam env --switch=.)"

opam install --yes --switch=. \
    dune.3.24.2 \
    ocamlformat.0.29.0 \
    merlin \
    ocaml-lsp-server

echo "AlphaStudio OCaml toolchain is ready."
