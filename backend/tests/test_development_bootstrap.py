from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_backend_editable_build_discovers_only_application_packages() -> None:
    configuration = tomllib.loads(
        (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["build-system"]["build-backend"] == "setuptools.build_meta"
    assert configuration["tool"]["setuptools"]["packages"]["find"]["include"] == ["app*"]


def test_windows_development_bootstrap_checks_dependencies_and_uses_pnpm() -> None:
    script = (ROOT / "scripts" / "dev.ps1").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in script
    assert "node_modules" in script
    assert "pnpm.cmd" in script
    assert "scripts\\dev.py" in script
    assert "本地开发：首次初始化" in readme
    assert "pip install -e \".[dev]\"" in readme
    assert "pnpm install --frozen-lockfile" in readme
    assert "npm.cmd" not in readme
