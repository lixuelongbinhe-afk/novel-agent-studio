#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/dev.py"
