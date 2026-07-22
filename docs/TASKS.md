# Tasks

## Unreleased - GitHub Issues #1 And #2

- [x] Add a PEP 517 setuptools build backend and restrict editable-install package discovery to `app*`.
- [x] Document the first-clone Windows setup, editable backend install, migration, and frozen pnpm install.
- [x] Make `dev.ps1` fail fast on missing dependencies and delegate process lifecycle management to `dev.py`.
- [x] Use pnpm consistently in development, build, start, and desktop packaging instructions.
- [x] Remove collapsed-sidebar arrow controls and make the in-bounds brand icon and status light expand the sidebar.
- [x] Cover bootstrap configuration and collapsed-sidebar behavior with backend, component, and Playwright regressions.

## V2.2.3 Chapter Tree And Editor Workflow Repair

- [x] Merge duplicate numbered volumes and insert missing generated chapters into the correct numeric volume range.
- [x] Detect and repair persisted duplicate volumes, out-of-order chapters, position gaps, missing chapters, and Agent-title placeholders behind explicit author confirmation.
- [x] Preserve chapter IDs, manuscript content, scenes, versions, and a permanent pre-repair snapshot.
- [x] Turn explicit editor workflow requests into confirmation cards and execute the real generation service only after approval.
- [x] Validate the repair against a copied 80-chapter production database without modifying the source database.

## Phase 1 - Project Skeleton And Novel Foundations

- [x] Create the repository structure, required documentation, environment example, Make targets, and Windows/POSIX scripts.
- [x] Add a formal Alembic revision and verify migration from an empty SQLite database.
- [x] Implement Project, Volume, Chapter, Scene, ChapterVersion, StoryEntity, EntityAlias, EntityRelation, EntityStateChange, TimelineEvent, Foreshadow, and StyleGuide.
- [x] Implement validated CRUD, ordering, soft delete, recovery, mixed-language word counts, autosave, version history, restore-before-snapshot, and optimistic concurrency.
- [x] Implement ProviderAccount, ProtocolConfiguration, ModelProfile, ModelCapability, and ModelPricing foundations without storing secret values.
- [x] Implement normalized gateway contracts, AdapterRegistry, and MockAdapter normal/JSON/usage/delay/timeout/rate-limit/error/stream modes.
- [x] Build the Chinese React UI for project home, three-pane TipTap writing workspace, library, model center, and recovery.
- [x] Add light/dark themes, desktop-first responsive layout, route-level code splitting, and stable narrow-screen behavior.
- [x] Backend tests: 5 passed; Ruff and mypy passed.
- [x] Frontend tests: 5 passed; TypeScript strict check passed.
- [x] Production build passed with route-level chunks and no size warning.
- [x] Playwright E2E passed against an isolated database: migrate, create, write, reload, soft delete, restore, desktop layout, and 390px layout.

## Phase 1 Limits

- Only MockAdapter can execute requests. Real provider traffic starts in phase 2.
- At the Phase 1 gate the deliverable was the local Web application. The supported Windows installer and portable ZIP were subsequently produced by the completed Phase 8 release process.

## Phase 2 - Multi Provider Protocols

- [x] Implement OpenAI Responses, OpenAI Chat Completions, Anthropic Messages, Gemini native, and Ollama native adapters.
- [x] Add editable presets for OpenAI, DeepSeek, xAI/Grok, Anthropic, Gemini, OpenRouter, Ollama, and generic OpenAI/Anthropic-compatible services.
- [x] Add shared `httpx.AsyncClient` infrastructure with pooling, bounded timeouts and response sizes, no redirects, request IDs, redacted headers, truncated errors, cancellation, and deterministic upstream closure.
- [x] Parse arbitrarily chunked SSE and NDJSON, including split UTF-8, multiline SSE, half-line NDJSON, malformed payloads, and interrupted streams.
- [x] Normalize authentication, permission, invalid request, missing model, unsupported capability, rate limit, quota, refusal, context, timeout, connection, interruption, malformed response, internal, and cancellation outcomes.
- [x] Complete the model center UI: Provider/preset/model create and edit, delete, connection test, model sync/manual add, and selected-provider normal/stream debugging.
- [x] Add deterministic local Fake Provider coverage for success, stream, structured output, tools, usage, 401/403/404/429/500/timeout, invalid JSON, HTML errors, interruption, and cancellation across all five protocols.
- [x] Verify the Phase 2 migration against the existing Phase 1 database and a new empty database; both reached `9f43d2a6c1b8` with nine presets.
- [x] Backend: 71 tests passed; Ruff and mypy passed.
- [x] Frontend: 7 tests passed; TypeScript strict check passed; production build passed.
- [x] Playwright E2E passed: create Mock Provider, test connection, sync model list, run normal/stream debug, plus the complete Phase 1 workflow and compact layout check.

## Phase 2 Limits

- Automated tests use local Fake Provider transports and never call paid endpoints. Live provider behavior still depends on the user's account, endpoint availability, and current vendor policy.
- Custom request/response mapping, SSRF approval, and manifest import/export belong to Phase 3.
- API key values remain process environment variables; only their variable names are persisted or returned.

## Phase 3 - Custom HTTP API

- [x] Add `generic_json_http` configuration for GET/POST, relative endpoints, query, headers, content type, typed request templates, parameter mappings, response mappings, stream mappings, error mappings, authentication, and capability defaults.
- [x] Support none, Bearer, API Key Header, custom Header, query credential, and Basic authentication through one explicitly bound `CredentialReference` only.
- [x] Reject executable/interpolated templates, unsafe JSONPath operators, absolute endpoints, URL credentials, static credential material, Cookie/Host injection, and secret-bearing manifests.
- [x] Parse JSON, Raw Text, SSE, NDJSON, and concatenated Chunked JSON incrementally, including split UTF-8 and cancellation-safe upstream closure.
- [x] Enforce SSRF controls: http/https only, no redirects, address validation, metadata/link-local/reserved blocking, DNS result validation, IP pinning, Host/SNI preservation, and exact-Origin approval for `local_private`.
- [x] Revoke approval, test state, and enablement after relevant Provider, authentication, credential, or adapter changes.
- [x] Add secret-free disabled manifest import/export and transactional Provider+adapter setup with rollback on failure.
- [x] Verify empty-database migration and safely upgrade unversioned Phase 1 legacy databases without data loss; incomplete legacy schemas fail explicitly instead of being stamped as current.
- [x] Build the Chinese custom API workbench with adapter editing, credential-reference create/edit/delete, exact-Origin approval, normal and cancellable stream debugging, redacted request/result views, enable gating, and manifest import/export.
- [x] Backend: 96 tests passed; Ruff and mypy passed. The current database is at `c31e6d7b924f`, contains all expected Phase 3 tables and nine presets, and produced zero common secret-pattern findings.
- [x] Frontend: TypeScript strict check passed.
- [x] Frontend: 7 Vitest files and 11 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed: 1,753 modules transformed, route-level chunks emitted, and no size warning.
- [x] Playwright E2E passed from an empty database: Phase 1/2 flow plus credential reference, transactional custom adapter setup, exact-Origin approval, redacted ordinary response, enablement, custom SSE stream, manifest download, desktop controls without overflow, and 390px layout.

## Phase 3 Limits

- Automated custom-provider tests use local ASGI transports or the local Fake Custom API and never call paid endpoints.
- Imported adapters and Providers are disabled. A credential reference, exact local Origin approval when applicable, and a current successful ordinary test are required before enablement.
- React Router emits documented v7 future-flag notices in unit tests; current v6 behavior is verified and unaffected.

## Phase 4 - Capability, Routing, Rate Limit, Cost

- [x] Add effective model capabilities with the required six-source priority, Provider/model enablement constraints, manual overrides, and no model-name guessing.
- [x] Implement bounded synthetic basic/standard/advanced probes with cancellation, request/output/cost limits, no manuscript data, no tool execution, and explicit advanced confirmation.
- [x] Implement native strict Schema, JSON Object plus local validation, prompted JSON plus safe incremental extraction, one separately metered repair, and explicit human-handling failure.
- [x] Reject text simulation for side-effecting tools and emit explicit warnings for every capability or streaming degradation.
- [x] Track token provenance in the required order and use only explicitly configured official/compatible tokenizers before local approximation.
- [x] Add context preflight with input/reserved/total/window/remaining values and the 80% warning, 95% strong warning, and 100% block thresholds.
- [x] Store non-overlapping pricing history for input, cached input, output, reasoning, request, and tool-call fees; unknown pricing remains unknown.
- [x] Enforce atomic concurrency, RPM, and TPM policies at global, project, Provider, model, Route, and Workflow scopes with queue timeout and cancellation.
- [x] Implement Retry-After handling, exponential backoff with jitter, Provider health, and closed/open/half-open circuit states; refusal and cancellation remain health-neutral.
- [x] Implement ordered fallback, lowest cost, lowest latency, healthiest, and manual-only Routes with project boundaries and the explicit retry/fallback whitelist.
- [x] Prevent fallback after partial streaming output and expose fallback, queue, warning, usage-source, and cost metadata to the UI and invocation ledger.
- [x] Enforce per-request, project-daily, and Route-per-run token/cost budgets in the central backend execution path before Provider calls.
- [x] Build the Chinese capability/price, Route, limit/budget, health/ledger, direct-model/Route debug, and preflight interfaces with working mutations and compact responsive layouts.
- [x] Backend: 117 tests passed; Ruff and strict mypy passed across 41 source files.
- [x] Frontend: 8 Vitest files and 13 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed: 1,754 modules transformed, route-level chunks emitted, and no size warning.
- [x] Playwright E2E passed from an empty database through migration `e47a1d8f2c60`: real capability override/clear, standard probe, pricing, Route, limits, budget, preflight, ordinary/stream calls, health, ledger, desktop visual capture, and 390px no-overflow capture.

## Phase 4 Limits

- Automated execution uses Mock/Fake Providers and never calls a paid endpoint. Live vendor behavior, tokenizer metadata, and prices must be configured and verified by the user.
- Rate and budget reservations are process-local and designed for the supported single-process local deployment; they are not a distributed quota service.
- Strict structured streaming is buffered until validation/repair succeeds. Unsupported native streaming can be explicitly emulated with a warning, and a stream that has emitted text is never silently joined to another model.
- React Router v7 future-flag notices and legacy pnpm-oriented npm config notices are non-failing toolchain warnings; current React Router v6 behavior and npm-based checks are verified.

## Phase 5 - Agent And DAG Workflow

- [x] Add project-owned Agent definitions with prompts, JSON Schemas, text/JSON output, exactly one fixed model or Route, parameters, capabilities/degradation, timeout, retry, budget, enablement, configuration hash, and semantic versioning.
- [x] Restrict prompt templates to safe workflow variables and reject attributes, private names, expressions, environment/credential/file/network access, imports, and executable syntax.
- [x] Add the Phase 5 Alembic revision for Workflows, nodes, edges, runs, node runs, Attempts, and persisted monotonic run events; verify empty and legacy migration classification.
- [x] Implement Start, Input Mapping, Agent, Merge, Condition, Text Template, Data Transform, and Output nodes with validated configuration and no executable user code.
- [x] Validate duplicate/invalid node keys, edge references, self-loops, concrete cycle paths, unique Start/Output, reachability, branch handles, Merge behavior, mappings/transforms, Agent/project/model/Route availability, prompts, and Schemas.
- [x] Compile valid graphs into immutable hash-addressed plans and freeze Workflow, Agent, model, Provider, protocol, Route, capability, pricing, active limit/budget, and input configuration without credentials.
- [x] Implement the asyncio DAG scheduler with dependency readiness, real overlapping independent nodes, active Condition branches, skipped inactive branches, active-dependency Merge, and final Output.
- [x] Route every Agent call through central execution for input validation, template rendering, capability/context/token/cost/budget preflight, queue/limit/circuit control, normalized streaming, output validation, usage/cost persistence, and downstream activation.
- [x] Persist independent NodeRunAttempt rows for retry, model invocation links, bounded partial output, normalized errors, usage, cost, and timestamps.
- [x] Implement persisted Workflow SSE with monotonic sequence IDs, event history, `Last-Event-ID`, reconnect replay, snapshot resync, and terminal completion.
- [x] Propagate idempotent cancellation through scheduling, queued work, and active model streams; preserve partial output, stop downstream activation, and release central reservations.
- [x] Implement parent-linked retry-node, retry-descendants, and clone-from-node derived runs without overwriting historical runs.
- [x] Add secret-free Workflow manifest export/import with Agent remapping, destination field limits, unique naming, and disabled imports.
- [x] Seed the required 11-Agent Mock workflow: goal analysis; parallel character/world/foreshadow/pacing; scene plan; draft; parallel continuity/dialogue/style; editor; Output.
- [x] Build the Chinese Agent table and complete create/edit/delete form, including target, prompts, Schemas, parameters, capabilities, degradation, retries, timeout, budget, status, version, and server errors.
- [x] Build the React Flow workbench with palette drag/click, handles, connect, delete, copy, multi-select, zoom, controls, minimap, undo/redo, inspector, save, import/export, validation, run input, and responsive layout.
- [x] Build run history and live monitoring with immutable graph/status overlays, node/Attempt inspection, partial/final output, usage/cost, event log, cancel, and three derived-run actions.
- [x] Mark startup-orphaned pending/running work interrupted and retain enough snapshots, attempts, partial output, and events for a derived recovery run.
- [x] Backend: 127 tests passed; Ruff passed; strict mypy passed across all 56 `app` and `tests` source files.
- [x] Frontend: 9 Vitest files and 15 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed: 1,918 modules transformed, route-level chunks emitted, and no size warning.
- [x] Playwright E2E passed from an empty database through migration `f8b2c4d6e810`: create Mock Provider/model, create Agent, create and validate Start-Agent-Output DAG, execute to real Mock completion, inspect output/run graph, and capture 1440x900 and 390x844 no-overflow views with the mobile Start and Output nodes visible.

## Phase 5 Limits

- Automated Workflow execution uses local Mock/Fake Providers and never calls paid endpoints. Live targets still depend on user-supplied non-secret configuration and environment credentials.
- Scheduling, live cancellation, rate reservations, and event fan-out run in one local backend process. Closing the process stops tasks; startup marks unfinished records interrupted instead of pretending they continued in the background.
- Persisted event/output history is intended for local creative work, not untrusted multi-tenant hosting. Phase 8 hardens local deployment but intentionally does not add public-service authentication or multi-tenancy.
- At the Phase 5 gate, context retrieval was not injected. Phase 6 now supplies explainable retrieval, classification, Provider data boundaries, token budgeting, preview controls, and a formal Context Retrieval node.
- Human Approval, state extraction, proposed changes, and transactional writeback remain Phase 7. Workflow output cannot currently modify novel records automatically.
- At the Phase 5 gate, Windows artifacts remained gated. The completed Phase 8 installer and portable ZIP are the only supported desktop deliverables.

## Phase 6 - Novel Memory And Context Retrieval

- [x] Add Chapter Summary, Scene State, Chapter/Entity Link, Context Pin, Content Classification, Context Policy, Provider Data Policy, and immutable Context Build models with revision/project-boundary validation.
- [x] Add Alembic revision `a6c8e0f2b419`, the eight relational tables, SQLite FTS5 `context_fts`, default project/Provider policies, and empty/legacy migration verification.
- [x] Implement current scene/capped chapter, entity/alias/tag/state/relation, manual link, recent chapter summary, timeline, foreshadow, rule/style, SQLite FTS, Pin, and composite retrievers with a disabled embedding extension point.
- [x] Index only project-owned active sources, parse rich manuscript HTML to visible text, deduplicate identities, cap historical fragments, and never load the whole novel by default.
- [x] Build deterministic context packages from project/chapter/scene/Agent/workflow/upstream/model/policy inputs with ordered blocks, source reasons, relevance, priorities, classifications, Token estimates, exclusions, truncations, boundaries, conflicts, and hashes.
- [x] Resolve direct models and Routes before assembly, use the smallest target context window, and intersect classification scope across the project policy and every possible target Provider.
- [x] Remove optional low-relevance/results-limit items first, prefer summaries and bounded fragments, record every truncation, and block when required/locked content exceeds budget or crosses a prohibited Provider boundary.
- [x] Persist immutable Context Build snapshots and verify that unchanged sources reproduce the same hash while later source edits do not alter historical snapshots.
- [x] Add real CRUD and revision-conflict APIs for memory records, policies, classifications, Pins and Provider boundaries, plus context build/list/read and FTS reindex endpoints.
- [x] Add a validated Context Retrieval workflow node that persists and emits a typed package, blocks downstream execution on conflict, and snapshots relevant policy/Provider configuration.
- [x] Let Agent nodes consume one upstream Context package or invoke the same builder automatically; strip the package from ordinary input and inject it exactly once as System context.
- [x] Build the Chinese Context workbench with Preview, Novel Memory, and Policy/Boundary views; real source inspection; Token/provider/hash metrics; include/exclude, lock, priority, Pin, classification, budget, and re-retrieval controls.
- [x] Build complete editors for chapter summaries, scene state, manual links, Pins, classifications, Context policies, and per-Provider data policies.
- [x] Add Context Retrieval to the React Flow palette and inspector with real Agent/model/policy/budget configuration and server-side validation.
- [x] Verify retrieval correctness/explanation, budgets/truncation/blocking, Route boundary intersection, immutable snapshots, project isolation, revision conflicts, workflow injection, automatic injection, and no story writes.
- [x] Backend: 133 tests passed; Ruff passed; strict mypy passed across all 63 `app` and `tests` source files.
- [x] Frontend: 10 Vitest files and 17 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed: 1,922 modules transformed, route-level Context chunk emitted, and no size warning.
- [x] Playwright E2E passed from an empty database through `a6c8e0f2b419`: real FTS source explanation, exclusion/restore, priority, lock, Pin, secret boundary block, HTML-free actual context, unchanged manuscript, and 1440x900/390x844 no-overflow screenshots.

## Phase 6 Limits

- SQLite FTS5 is lexical retrieval. CJK/term segmentation and semantic recall are intentionally limited; the embedding protocol is reserved but no external vector database is enabled by default.
- Context Token estimates use the local estimator at assembly time. Central model preflight still performs its configured Provider/tokenizer accounting before invocation.
- Provider data policies enforce local routing decisions but cannot attest to a vendor's retention or training practices. Users must configure and verify live Provider terms; automated checks call only local Mock/Fake Providers.
- Context Build rows may contain classified unpublished text in local SQLite. Phase 8 adds controlled backup/export and local hardening, but encryption, secure deletion, authentication, and public deployment remain outside v1.0.0.
- Phase 6 never auto-updates story records. Human Approval, extraction, proposed changes, conflict resolution, and transactional writeback are Phase 7.
- At the Phase 6 gate, Windows artifacts remained gated; the completed Phase 8 artifacts now carry the release audit evidence.

## Phase 7 - Human Approval And Safe Writeback

- [x] Add immutable Approval Request snapshots with pending, approved, changes-requested, rejected, expired, cancelled, and superseded states; revision checks; expiration; decision idempotency; and replacement links.
- [x] Add Human Approval workflow nodes that persist the snapshot, move the run and node to `waiting_approval`, emit durable SSE events, and resume the same run only after a valid decision.
- [x] Support approve, request changes, reject, and edit. A change request creates a new Agent Attempt and replacement approval without overwriting earlier output, with a maximum of three rounds.
- [x] Add State Extraction nodes with the canonical chapter/scene/entity/relation/timeline/foreshadow/conflict Schema, local validation, and at most one bounded repair.
- [x] Resolve entities in the required order: explicit ID, exact name, alias, manual chapter link, then one unique high-confidence candidate. Ambiguity remains visible and unmatched entities become creation candidates.
- [x] Add Proposed Change Sets containing only whitelisted chapter content/summary, scene synopsis/state, entity, alias, relation, state-change, timeline, and foreshadow operations. Arbitrary SQL, table names, and fields are rejected.
- [x] Freeze base values, revisions, evidence, confidence, resolution details, per-item decisions, conflicts, and a content hash for every Change Set.
- [x] Add explicit re-extract, rebase-on-current, manual-merge, and abandon conflict paths. Current database values are rechecked immediately before writeback and are never silently overwritten.
- [x] Add Database Writeback nodes that verify the approved immutable snapshot, Change Set hash/revision, cancellation state, and live record revisions before writing.
- [x] Create a protective chapter version and apply accepted changes, audit entries, record revisions, and FTS rebuild in one database transaction; injected FTS failure proves total rollback.
- [x] Add the Chinese Approval and Writeback workbench with a pending/history queue, frozen prose Diff, decision notes, edit/request/reject actions, per-item Change Set controls, conflict console, and append-only audit inspection.
- [x] Add Human Approval, State Extraction, Proposed Changes, and Database Writeback nodes to the React Flow palette, inspector, validation, saved graph, run overlay, and waiting-state labels.
- [x] Verify cancellation, expiration, stale revisions, superseded snapshots, idempotent decisions, maximum rounds, project boundaries, entity ambiguity, SQL-field rejection, no-write-before-approval, live conflicts, reapproval, rollback, versions, audits, and same-run resume.
- [x] Backend: 146 tests passed; Ruff passed; strict mypy passed across all 74 `app` and `tests` source files.
- [x] Frontend: 11 Vitest files and 19 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed with a route-level Approval chunk and no size warning.
- [x] Playwright E2E passed from an empty database through `d7e9f1a3c520`: real two-pause workflow, unchanged chapter before both approvals, frozen prose Diff, Change Set edit and replacement approval, same-run transaction writeback, chapter version, audit, and 1440x900/390x844 no-overflow screenshots.

## Phase 7 Limits

- Automated Agent execution uses the local Mock adapter and never contacts a paid endpoint. Live extraction quality and availability depend on the selected user-configured Provider, while local Schema validation and one-repair limits remain enforced.
- Workflow waits and signal fan-out are in-process. Closing the application interrupts the run; persisted approvals, attempts, snapshots, and audits remain available, but the process does not pretend background execution continued.
- Append-only audit behavior is enforced through the application service and API. A user with direct filesystem/database access remains inside the supported local trust boundary after Phase 8 hardening.
- At the Phase 7 gate, Windows artifacts remained gated; only the completed Phase 8 installer and portable ZIP are supported deliverables.

## Phase 8 - Hardening, Backup, Release

- [x] Add complete versioned backup archives with manifest/data hashes, table counts, Secret scanning, preview, size/entry/compression-ratio/path controls, Schema validation, and migration hooks.
- [x] Implement replace-all restore with explicit SHA-256 confirmation, one database transaction, FTS rebuild, rollback on simulated disk failure, and immutable preflight preview.
- [x] Add real book/chapter Markdown, library JSON, timeline CSV, foreshadow JSON, Agent JSON, Workflow JSON, Adapter JSON, and privacy-safe diagnostics ZIP exports.
- [x] Add release status, database integrity, log listing/download/delete, bounded rotating logs, startup migration, interrupted-run recovery, and Chinese release/recovery UI.
- [x] Harden production Host/Origin behavior, CORS, security Headers, API cache policy, upload MIME/size streaming, archive handling, SSRF regressions, diagnostics privacy, and production API-doc exposure.
- [x] Lock backend dependencies and retain frontend lockfiles; verify packaged dependency collection and bundled frontend integrity.
- [x] Optimize Workflow history with cursor pagination, stream rendering with animation-frame batching, React Flow visible-element rendering, and entity-state retrieval without N+1 queries.
- [x] Add performance gates for a 100-node DAG, 100,000-character chapter autosave, and 1,000-entity retrieval with bounded SQL statements.
- [x] Build a real PyInstaller Windows application with embedded React/FastAPI/SQLite runtime, random loopback port, instance mutex, dedicated Edge profile, runtime diagnostics, and controlled shutdown.
- [x] Fix the Edge bootstrap handoff lifecycle regression by tracking the actual Windows app window instead of waiting on the initial Edge process.
- [x] Add a C# installer/uninstaller, Start Menu registration, uninstall registry metadata, data preservation choice, portable mode, ZIP, installer payload hash validation, and SHA-256 release manifest.
- [x] Run packaged console and real GUI lifecycle smoke tests; verify installation, portable extraction, 15-second health retention, window-close shutdown, uninstall/data preservation, reinstall, and retained-data startup.
- [x] Backend: 157 tests passed; Ruff passed; strict mypy passed across 83 source and test files.
- [x] Frontend: 12 Vitest files and 22 tests passed; TypeScript strict check passed.
- [x] Production Vite build passed with 1,925 modules and no chunk-size warning; PyInstaller build passed.
- [x] Final Playwright E2E passed from an empty database through Seed, writing, model/Agent/Workflow execution, context, two approvals, transactional writeback, version restore, backup, deletion, restore, and consistency verification.
- [x] Complete README, changelog, final audit, security audit, performance audit, release checklist, and known limitations.

## Phase 8 Limits

- The supported release is a local single-user Windows x64 application. It is not an authenticated, TLS-enabled, multi-tenant, or public service.
- The installer is not commercially code-signed and depends on Microsoft Edge or WebView2 Runtime.
- SQLite, logs, and backups are not encrypted at rest. Complete backups contain manuscript data and must be protected by the user.
- Live Provider availability and policy remain external; automated tests never call paid APIs.
- Closing the application stops active work. Startup marks unfinished runs interrupted and preserves snapshots and recovery paths instead of claiming background continuation.

## V2.1 Confirmed Creation Workflow

- [x] Add creative and imported-outline entry flows with independent multi-Agent planning artifacts.
- [x] Gate prose generation on approval of every current planning artifact.
- [x] Add chapter/scene review, per-scene generation and transactional chapter composition.
- [x] Add user-owned style-reference extraction for TXT, Markdown and Word.
- [x] Add per-item regeneration, superseded version chains, side-by-side comparison, restore-to-editor and review notes.
- [x] Add minor automatic conflict correction and explicit major-conflict author decisions.
- [x] Add manual, automatic and pausable countdown continuation after approval.
- [x] Mark orphaned studio generation jobs interrupted on startup and preserve tray execution.
- [x] Verify 169 backend tests, 28 frontend tests, Ruff, strict mypy, TypeScript, production build and 2 Playwright E2E tests.
- [x] Fix manuscript review writeback controls and refresh the open chapter editor after an approved Agent draft changes the server revision.
- [x] Record the complete acceptance matrix in `docs/V2_REQUIREMENTS_ACCEPTANCE.md`.
- [x] Fix long chapter plans by batching each planning Agent in groups of ten and enforcing the configured chapter count.
- [x] Add confirmed, permanently snapshotted repair for legacy Agent-title chapters and missing chapter placeholders.
- [x] Keep the manuscript editor available during full-book review, with chapter create/delete and real save controls.
- [x] Match production `autoflush=False` in Studio regression tests and explicitly flush writeback before stage, scene and snapshot queries.

## V2.2 Half-Finished Novel Continuation

- [x] Import TXT, Markdown, DOCX, text-based PDF, pasted prose, or an existing project into a continuation project.
- [x] Preserve the complete imported manuscript as an immutable artifact and permanent project snapshot while creating an editable chapter tree.
- [x] Add the seven-stage continuation workflow with per-item author approval gates.
- [x] Extract structure, world rules, character relations, timeline, foreshadows, style, and unresolved plotlines with independent Agents.
- [x] Reconstruct existing volume/chapter/scene outlines and create future chapter placeholders only after the continuation plan is approved.
- [x] Support author/AI direction switching and manual or AI-suggested word, chapter, and volume targets.
- [x] Require an explicit current-chapter/next-chapter choice; append current-chapter continuation without replacing imported prose.
- [x] Pause after a major continuity conflict until the author chooses a resolution; retain approval-only AI edits and protective snapshots.
- [x] Add backend and frontend regressions for import, PDF extraction, immutable originals, approval gates, planning, append writeback, conflict pause, entry UI, and start choice.
- [x] Verify 176 backend tests, 31 frontend tests, Ruff, strict mypy, TypeScript, production build and 3 Playwright E2E tests.

## V2.2.1 Context Window And Right Rail

- [x] Use the selected model profile's context window and reserve bounded output before every Studio model call.
- [x] Build Studio context through ContextBuilder retrieval and record included, excluded and truncated context metadata.
- [x] Chunk long manuscripts and style references, share Map results across continuation Agents, and synthesize bounded summaries.
- [x] Recompress and retry when a Provider still reports `context_too_long`, without silently changing models.
- [x] Keep long chat replies inside an independently scrolling message stream with the composer fixed at the rail bottom.
- [x] Verify 179 backend tests, 32 frontend tests and the 18-message responsive Playwright regression.

## V2.2.2 Button And Project Deletion Audit

- [x] Fix the nested SQLAlchemy transaction that prevented project deletion from reaching the database.
- [x] Surface project deletion failures and keep the row visible when persistence fails.
- [x] Audit all 104 button declarations reachable from the current desktop routes and reject missing or empty handlers in CI.
- [x] Exercise project deletion through component, backend-route, and persisted Playwright regressions.
- [x] Exercise sidebar navigation, provider lifecycle, custom adapter lifecycle, manifest import/export, and credential deletion.
- [x] Verify 180 backend tests, 41 frontend tests, Ruff, strict mypy, and TypeScript strict checking.
