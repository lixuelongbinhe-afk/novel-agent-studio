from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.approvals import router as approvals_router
from app.api.custom_api import router as custom_api_router
from app.api.context import router as context_router
from app.api.model_center import router as model_center_router
from app.api.model_control import router as model_control_router
from app.api.novels import router as novels_router
from app.api.release import router as release_router
from app.api.studio import router as studio_router
from app.api.workflows import router as workflows_router
from app.core.config import get_settings
from app.core.logging_config import cleanup_log_files, configure_logging
from app.core.security import LocalOriginMiddleware, SecurityHeadersMiddleware
from app.database import SessionLocal
from app.migrations import upgrade_database
from app.services.gateway_http import shared_http_client
from app.services.studio import mark_interrupted_generation_jobs
from app.services.workflows import mark_interrupted_runs

settings = get_settings()


def _frontend_dist() -> Path | None:
    candidates: list[Path] = []
    if settings.frontend_dist:
        candidates.append(Path(settings.frontend_dist).expanduser())
    bundle_root = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_root, str):
        candidates.append(Path(bundle_root) / "frontend-dist")
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "index.html").is_file():
            return resolved
    return None


frontend_dist = _frontend_dist()


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    cleanup_log_files(delete_all=False)
    upgrade_database()
    with SessionLocal() as db, db.begin():
        mark_interrupted_runs(db)
        mark_interrupted_generation_jobs(db)
    try:
        yield
    finally:
        await shared_http_client.aclose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url=None if settings.production else "/docs",
    redoc_url=None if settings.production else "/redoc",
    openapi_url=None if settings.production else "/openapi.json",
)
app.state.frontend_bundled = frontend_dist is not None
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LocalOriginMiddleware, allowed_origins=settings.cors_origin_list)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


app.include_router(novels_router, prefix="/api")
app.include_router(model_center_router, prefix="/api")
app.include_router(model_control_router, prefix="/api")
app.include_router(custom_api_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(context_router, prefix="/api")
app.include_router(approvals_router, prefix="/api")
app.include_router(release_router, prefix="/api")
app.include_router(studio_router, prefix="/api")


frontend_root = frontend_dist
if frontend_root is not None:
    bundled_root = frontend_root
    assets_dir = bundled_root / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/") or full_path in {"api", "health"}:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (bundled_root / full_path).resolve()
        if candidate.is_relative_to(bundled_root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(bundled_root / "index.html", headers={"Cache-Control": "no-cache"})
