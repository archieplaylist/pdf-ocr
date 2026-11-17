#!/usr/bin/env bash
# Run the project venv with NixOS wheel libraries on LD_LIBRARY_PATH.
set -euo pipefail
cd "$(dirname "$0")"
GCCLIB="${GCCLIB:-/nix/store/0ba5kj6r0zhfwd84l5vzccqvx7amlq2c-gcc-15.3.0-lib}"
ZLIB="${ZLIB:-/nix/store/7b063z98zcilpmzj2sz7sl56gl84zf02-zlib-1.3.2}"
export LD_LIBRARY_PATH="$GCCLIB/lib:$ZLIB/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec .venv/bin/python "$@"
