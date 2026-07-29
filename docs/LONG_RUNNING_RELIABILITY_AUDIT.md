# Long-Running Reliability Audit

Audit date: 2026-07-29

Scope: a single desktop process kept open for six months, large novels, thousands of workflow runs, repeated model-policy edits, long approval waits, network outages, and continuous generation.

## Fixed Findings

| Priority | Finding | Failure mode | Remediation |
| --- | --- | --- | --- |
| P1 | Retention ran only at startup | Continuous uptime bypassed event/context/log retention and WAL maintenance | Added a six-hour runtime maintenance loop with clean shutdown and failure isolation |
| P1 | Workflow locks were retained by run ID | Memory grew with every workflow ever executed | Replaced the strong lock map with weakly held active locks |
| P1 | Approval signals survived completed waits | Memory grew with every approval and orphan notifications | Notifications now wake only active waiters; wait entries are always removed |
| P1 | Rate-limit policy state was never evicted | Repeated policy creation/deletion accumulated state forever | Empty concurrency state is removed immediately and expired RPM/TPM windows are evicted |
| P1 | Workflow snapshots returned the oldest 2,000 events | Completed long runs showed stale logs and transferred large payloads | Snapshots now return the latest 500 events in ascending order |
| P1 | Browser event arrays grew without limit | A long stream or reconnect history increased memory and render work indefinitely | Retained event history is capped at 500; the UI still displays the latest 250 |
| P1 | Project overview loaded every superseded artifact body | Editing/regeneration made project open time and memory grow with all history | Overview returns current artifacts; complete series history remains available on demand |
| P1 | Snapshot metadata listing loaded every full snapshot payload | Permanent snapshots made each project open increasingly expensive | Metadata queries defer snapshot payloads until an actual restore |
| P2 | SSE polled SQLite every 150 ms while idle | Days-long approval waits caused avoidable database and CPU load | Idle polling backs off to 1.5 seconds and resets immediately when events arrive |
| P2 | Retention queries lacked data-age composite indexes | Cleanup degraded toward full scans as event/context tables grew | Added event type/date/run and context project/date/id indexes |

## Preserved Limits

- Permanent and important-turn snapshots remain permanent by product requirement. They are never deleted automatically, so storage usage must remain visible and backups must be tested periodically.
- SQLite remains a local single-user database with one serialized write path. The scheduler bounds concurrency; this is not a multi-user server architecture.
- Backup archives are capped by `NAS_MAX_BACKUP_BYTES` and `NAS_MAX_BACKUP_UNCOMPRESSED_BYTES`. Very large retained histories can require raising those limits on a machine with enough disk and memory.
- The scheduled soak test exercises concurrency and recovery for two hours. It is a regression signal, not proof equivalent to six months of wall-clock uptime.

## Verification Gates

- Backend: full pytest suite, Ruff, strict mypy, Alembic upgrade/rollback tests.
- Frontend: full Vitest suite, TypeScript build check, production Vite build.
- Stress regressions: 10,000 stream deltas, 200 expired rate-limit policies, 650-event snapshot tail, repeated maintenance cycles, lock/signal reclamation.
