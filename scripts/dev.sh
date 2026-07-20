#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
(cd "$ROOT/backend" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) &
(cd "$ROOT/frontend" && pnpm dev --host 127.0.0.1 --port 5173) &
wait

