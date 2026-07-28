# Security summary

Generated: `2026-07-27T20:02:39.168622+00:00`  
Version: `2.2.7`  
Commit: `33107baedc31a9c25a4d4e6e90d7db853f1bdf40`

- Python runtime lock: `pip-audit` passed with no known vulnerabilities.
- Frontend lock: `pnpm audit --audit-level high` passed.
- Temporary exception: `GHSA-qwww-vcr4-c8h2` is limited to unused React Router RSC server actions and has no published compatible fix.
- Dependency inputs are hash locked; GitHub Actions are pinned to full commit SHAs.
- Release output includes SHA-256 checksums and a CycloneDX SBOM.
