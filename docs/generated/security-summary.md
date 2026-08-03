# Security summary

Generated: `2026-07-28T06:00:52.536432+00:00`  
Version: `2.2.8`  
Commit: `ad5661fd0f443039f90815ef452b5c7154cdb2cd`

- Python runtime lock: `pip-audit` passed with no known vulnerabilities.
- Frontend lock: `pnpm audit --audit-level high` passed.
- Temporary exception: `GHSA-qwww-vcr4-c8h2` is limited to unused React Router RSC server actions; React Router 7.18.2 was rechecked on 2026-08-03 and remains affected. Review again by 2027-01-31.
- Dependency inputs are hash locked; GitHub Actions are pinned to full commit SHAs.
- Release output includes SHA-256 checksums and a CycloneDX SBOM.
