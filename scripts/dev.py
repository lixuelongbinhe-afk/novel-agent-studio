import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
        cwd=ROOT / "backend",
    )
    frontend = subprocess.Popen(["pnpm", "dev", "--host", "127.0.0.1", "--port", "5173"], cwd=ROOT / "frontend")
    try:
        return backend.wait() if backend.poll() is None else frontend.wait()
    except KeyboardInterrupt:
        backend.terminate()
        frontend.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

