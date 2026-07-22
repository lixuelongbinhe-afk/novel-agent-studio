from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
FRONTEND_INPUTS = (
    "frontend/src",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "frontend/postcss.config.js",
    "frontend/tailwind.config.js",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_paths(paths: tuple[str, ...]) -> str:
    files: list[Path] = []
    for relative in paths:
        candidate = ROOT / relative
        if candidate.is_dir():
            files.extend(path for path in candidate.rglob("*") if path.is_file())
        elif candidate.is_file():
            files.append(candidate)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.rstrip()


def verify_versions() -> None:
    if not VERSION_PATTERN.fullmatch(VERSION):
        raise RuntimeError("VERSION must use MAJOR.MINOR.PATCH")
    checks = {
        "backend/pyproject.toml": rf'^version = "{re.escape(VERSION)}"$',
        "backend/app/core/config.py": rf'app_version: str = Field\("{re.escape(VERSION)}"',
        "desktop/launcher.py": rf'^VERSION = "{re.escape(VERSION)}"$',
        "scripts/NovelAgentStudioInstaller.cs": rf'AssemblyVersion\("{re.escape(VERSION)}\.0"\)',
        "build/windows-version.txt": rf"filevers=\({VERSION.replace('.', ', ')}, 0\)",
    }
    errors: list[str] = []
    for relative, pattern in checks.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        if re.search(pattern, text, re.MULTILINE) is None:
            errors.append(relative)
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    if package.get("version") != VERSION:
        errors.append("frontend/package.json")
    installer = (ROOT / "scripts/NovelAgentStudioInstaller.cs").read_text(encoding="utf-8")
    if f'internal const string Version = "{VERSION}";' not in installer:
        errors.append("scripts/NovelAgentStudioInstaller.cs const")
    if errors:
        raise RuntimeError(f"Version {VERSION} is not synchronized: {', '.join(errors)}")


def build_manifest(*, allow_dirty: bool, allow_untagged: bool) -> dict[str, Any]:
    verify_versions()
    commit = _git("rev-parse", "HEAD")
    dirty_lines = [
        line
        for line in _git("status", "--porcelain", "--untracked-files=all").splitlines()
        if line
    ]
    if dirty_lines and not allow_dirty:
        raise RuntimeError("Release builds require a clean Git working tree")
    expected_tag = f"v{VERSION}"
    tags = set(_git("tag", "--points-at", "HEAD").splitlines())
    if expected_tag not in tags and not allow_untagged:
        raise RuntimeError(f"Release commit must have the exact tag {expected_tag}")
    epoch_text = os.getenv("SOURCE_DATE_EPOCH") or _git("show", "-s", "--format=%ct", commit)
    built_at = datetime.fromtimestamp(int(epoch_text), timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "product": "Novel Agent Studio",
        "version": VERSION,
        "commit": commit,
        "expected_tag": expected_tag,
        "tag_verified": expected_tag in tags,
        "dirty": bool(dirty_lines),
        "dirty_paths": [line[3:] for line in dirty_lines],
        "source_sha256": hash_git_sources(),
        "frontend_source_sha256": hash_paths(FRONTEND_INPUTS),
        "frontend_lock_sha256": sha256_file(ROOT / "frontend/pnpm-lock.yaml"),
        "backend_manifest_sha256": sha256_file(ROOT / "backend/pyproject.toml"),
        "source_date_epoch": int(epoch_text),
        "built_at": built_at,
    }


def hash_git_sources() -> str:
    names = _git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    digest = hashlib.sha256()
    for name in sorted(names):
        path = ROOT / name
        if not path.is_file():
            continue
        encoded = name.replace("\\", "/").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify")
    subparsers.add_parser("frontend-hash")
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--allow-dirty", action="store_true")
    manifest.add_argument("--allow-untagged", action="store_true")
    args = parser.parse_args()
    if args.command == "verify":
        verify_versions()
        print(VERSION)
    elif args.command == "frontend-hash":
        print(hash_paths(FRONTEND_INPUTS))
    else:
        payload = build_manifest(
            allow_dirty=bool(args.allow_dirty),
            allow_untagged=bool(args.allow_untagged),
        )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
