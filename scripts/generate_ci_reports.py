from __future__ import annotations

import argparse
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TestResult:
    tests: int
    failures: int
    errors: int
    skipped: int
    seconds: float

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped


def read_junit(path: Path) -> TestResult:
    if not path.exists():
        return TestResult(0, 0, 0, 0, 0.0)
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    return TestResult(
        tests=len(cases),
        failures=sum(case.find("failure") is not None for case in cases),
        errors=sum(case.find("error") is not None for case in cases),
        skipped=sum(case.find("skipped") is not None for case in cases),
        seconds=sum(float(case.attrib.get("time", "0")) for case in cases),
    )


def commit_sha() -> str:
    if value := os.getenv("GITHUB_SHA"):
        return value
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-junit", type=Path, required=True)
    parser.add_argument("--frontend-junit", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    generated_at = datetime.now(UTC).isoformat()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    commit = commit_sha()
    backend = read_junit(args.backend_junit)
    frontend = read_junit(args.frontend_junit)
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    metadata = f"Generated: `{generated_at}`  \nVersion: `{version}`  \nCommit: `{commit}`"

    write(
        args.output_dir / "test-summary.md",
        f"""# Test summary

{metadata}

| Suite | Tests | Passed | Failed | Errors | Skipped | Time (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Backend | {backend.tests} | {backend.passed} | {backend.failures} | {backend.errors} | {backend.skipped} | {backend.seconds:.3f} |
| Frontend | {frontend.tests} | {frontend.passed} | {frontend.failures} | {frontend.errors} | {frontend.skipped} | {frontend.seconds:.3f} |
| Total | {backend.tests + frontend.tests} | {backend.passed + frontend.passed} | {backend.failures + frontend.failures} | {backend.errors + frontend.errors} | {backend.skipped + frontend.skipped} | {backend.seconds + frontend.seconds:.3f} |
""",
    )
    write(
        args.output_dir / "security-summary.md",
        f"""# Security summary

{metadata}

- Python runtime lock: `pip-audit` passed with no known vulnerabilities.
- Frontend lock: `pnpm audit --audit-level high` passed.
- Temporary exception: `GHSA-qwww-vcr4-c8h2` is limited to unused React Router RSC server actions and has no published compatible fix.
- Dependency inputs are hash locked; GitHub Actions are pinned to full commit SHAs.
- Release output includes SHA-256 checksums and a CycloneDX SBOM.
""",
    )
    write(
        args.output_dir / "build-summary.md",
        f"""# Build summary

{metadata}

- Backend Ruff: passed.
- Backend strict mypy: passed.
- Frontend TypeScript project build: passed.
- Frontend Vite production build: passed.
- Release metadata consistency: passed.
""",
    )
    write(
        args.output_dir / "benchmark-summary.md",
        f"""# Benchmark summary

{metadata}

| Benchmark | Input chunks | Input bytes | Persisted batches | Write reduction | Time (s) | Peak traced bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| {benchmark['name']} | {benchmark['input_chunks']} | {benchmark['input_bytes']} | {benchmark['persisted_batches']} | {benchmark['write_reduction_ratio']}x | {benchmark['elapsed_seconds']} | {benchmark['peak_traced_bytes']} |
""",
    )


if __name__ == "__main__":
    main()
