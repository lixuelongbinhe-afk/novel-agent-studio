import os
import subprocess
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require_development_environment() -> tuple[Path, str]:
    backend_python = (
        ROOT
        / "backend"
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not backend_python.is_file():
        raise RuntimeError(
            "Missing backend virtual environment. Follow README '本地开发：首次初始化' first."
        )
    if not (ROOT / "frontend" / "node_modules").is_dir():
        raise RuntimeError(
            "Missing frontend dependencies. Run 'pnpm install --frozen-lockfile' in frontend."
        )
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm was not found. Enable Corepack or install pnpm 11.")
    return backend_python, pnpm


def main() -> int:
    try:
        backend_python, pnpm = require_development_environment()
    except RuntimeError as exc:
        print(f"Development environment is incomplete: {exc}")
        return 2
    backend = subprocess.Popen(
        [str(backend_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
        cwd=ROOT / "backend",
    )
    try:
        frontend = subprocess.Popen(
            [pnpm, "dev", "--host", "127.0.0.1", "--port", "5173"],
            cwd=ROOT / "frontend",
        )
    except Exception:
        backend.terminate()
        backend.wait(timeout=5)
        raise
    print("Novel Agent Studio started: backend http://127.0.0.1:8000, frontend http://127.0.0.1:5173")
    try:
        while True:
            if backend.poll() is not None:
                return int(backend.returncode or 0)
            if frontend.poll() is not None:
                return int(frontend.returncode or 0)
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in (backend, frontend):
            if process.poll() is None:
                process.terminate()
        for process in (backend, frontend):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
