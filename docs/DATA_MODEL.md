# Data Model

The novel foundation tables are projects, volumes, chapters, scenes, chapter versions, story entities, aliases, relations, entity state changes, timeline events, foreshadows, and style guides. Provider configuration uses provider accounts, protocol configurations, model profiles, model capabilities, model pricing, credential references, generic HTTP adapters, and Origin approvals.

Project-owned ordered records use integer `position`. Mutable records use `revision` for optimistic concurrency and `deleted_at` for reversible soft deletion. Child records are not physically removed when a parent is soft deleted.

Chapter and scene content is stored as TipTap-compatible HTML. Word counts parse visible text instead of counting markup. Every chapter autosave snapshots the previous value. Version restore first snapshots the current value, then applies the selected version in the same transaction.

Phase 4 adds capability probe runs, model routes and ordered route entries, rate-limit policies, budget policies, Provider health, and model invocations. Model profiles store an explicit tokenizer name and source; no tokenizer is inferred from the model name. Model pricing stores historical effective intervals and nullable input, cached-input, output, reasoning, request, and tool-call prices. A null price means unknown, not zero.

Route entries preserve model order and Route revision. Rate-limit scopes use a type/key pair for global, project, Provider, model, Route, or Workflow matching. Budgets use the same explicit scope pattern for per-request, project-daily, and Route-per-run enforcement. Invocation rows record request identity, selected Route/model, attempt and fallback counts, status/error, queue and latency, token source, actual/estimated usage, and known/unknown cost without storing credentials or hidden reasoning.

Phase 5 adds `agent_definitions`, `workflows`, `workflow_nodes`, `workflow_edges`, `workflow_runs`, `node_runs`, `node_run_attempts`, and `workflow_run_events`. Agent critical configuration is hash-addressed and versioned independently from display-name or enable-state changes. Workflow draft rows remain mutable under optimistic revision control, while every run stores immutable plan and configuration snapshots.

Node attempts are append-only historical records with input/output, bounded partial text, normalized error, linked invocation IDs, token totals, known/unknown cost, and timestamps. Derived runs point to a parent and resume node but never replace the source run. Event sequences are unique and monotonic per run, enabling persisted SSE replay and snapshot resynchronization.

Phase 6 adds `chapter_summaries`, `scene_states`, `chapter_entity_links`, `context_pins`, `content_classifications`, `context_policies`, `provider_data_policies`, `context_builds`, and the SQLite FTS5 virtual table `context_fts`. Memory/control records use revision checks and soft deletion. A Context Build is append-only and stores its exact request, rendered context, structured result, target Provider IDs, and hash so later source edits cannot rewrite historical execution evidence.

Classification values are `public`, `internal`, `confidential`, `personal information`, `sensitive personal information`, `unpublished manuscript`, and `secret`. Context policies are project-owned; Provider data policies are Provider-owned. Route execution uses the intersection across every possible target Provider instead of assuming the first candidate will be selected.

Phase 7 adds `approval_requests`, `proposed_change_sets`, and `writeback_audits`. Approval rows store an immutable serialized snapshot and hash, snapshot revision, approval round, parent/replacement links, expiry, idempotent decision metadata, and optimistic revision. Change Sets store canonical extraction, base revisions, whitelisted items, conflicts, current hash, lifecycle state, and replacement/application links. Writeback audits are append-only application records tied to the run, approval, and exact Change Set hash, with before/applied evidence for each committed item.

The current Alembic head is `d7e9f1a3c520`. Empty databases and proven complete legacy Phase 1/2/3/4/5/6 schemas migrate through every revision; incomplete unversioned schemas fail without being stamped current.
