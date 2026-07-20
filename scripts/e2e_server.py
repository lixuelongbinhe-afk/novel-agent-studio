from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATABASE = ROOT / "work" / "e2e.db"

DATABASE.parent.mkdir(parents=True, exist_ok=True)
DATABASE.unlink(missing_ok=True)
os.environ["NAS_DATABASE_URL"] = f"sqlite:///{DATABASE.as_posix()}"
os.environ["NAS_CORS_ORIGINS"] = "http://127.0.0.1:5174"
os.environ["E2E_CUSTOM_API_KEY"] = "e2e-custom-secret"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

uvicorn.run("app.main:app", host="127.0.0.1", port=8010, log_level="warning")
