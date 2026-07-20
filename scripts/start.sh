#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$ROOT/frontend/dist/index.html" || {
  echo "Missing frontend production build. Run npm run build in frontend first." >&2
  exit 1
}
export NAS_ENV=production
export NAS_FRONTEND_DIST="$ROOT/frontend/dist"
export NAS_CORS_ORIGINS=""
export NAS_ALLOWED_HOSTS="127.0.0.1,localhost"
echo "Novel Agent Studio: http://127.0.0.1:8000"
cd "$ROOT/backend"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-server-header
