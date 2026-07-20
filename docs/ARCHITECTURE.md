# Architecture

Novel Agent Studio is a local-first React/FastAPI application. The browser UI talks only to the local FastAPI API; SQLite is the durable source of truth.

## Frontend

- React + TypeScript strict + Vite + Tailwind
- React Router with route-level lazy loading
- TanStack Query for server state and mutation invalidation
- Zustand for persisted UI selection/theme state
- TipTap for chapter and scene editing
- Vitest for components and Playwright for isolated end-to-end workflows

## Backend

- FastAPI routes validate Pydantic v2 schemas.
- Services own domain rules and optimistic concurrency.
- Repository helpers own common SQLAlchemy access, soft deletion, and word extraction.
- SQLAlchemy 2 models are migrated by Alembic into SQLite with foreign keys enabled.
- Model adapters implement normalized complete/stream contracts through an AdapterRegistry.

All write routes open explicit transactions.

## Model Execution Control

`ModelExecutionService` is the only production path used by model-center ordinary and streaming calls. It resolves a direct model or immutable Route order, computes effective capabilities, applies safe degradation, performs context/token/cost preflight, reserves budgets and six-scope rate-limit capacity, checks Provider circuit state, invokes the adapter, records usage and cost, and releases reservations. Retry and fallback decisions use normalized error codes and an explicit allowlist.

Capability, pricing, Route, limit, budget, and health services own their persisted configuration. `RoutingService`, `UsageControlService`, `RateLimitManager`, and `StructuredOutputService` remain independent of protocol serialization. Adapters only translate normalized requests and responses; they do not decide budgets, fallback, or data ownership.

Every attempted model call, including a limited structured-output repair, has its own `ModelInvocation` ledger row. Streams that have emitted user-visible text cannot switch models. Structured streams are validated before release when local validation or repair is required.

## Agent And Workflow Runtime

Agent definitions are project-owned, versioned configurations. Prompt templates use a restricted formatter over `input`, `nodes`, `project`, `run`, `upstream`, and `value`; rendering never evaluates attributes, code, environment variables, files, or network access. A definition selects exactly one fixed model or Route and keeps Schema, capability, timeout, retry, and budget rules together.

Workflow drafts persist normalized nodes and edges. Validation checks identities, references, unique Start/Output, reachability, concrete cycle paths, node configuration, Agent targets, schemas, Condition branches, Merge behavior, mappings, and templates. A valid graph compiles to a hash-addressed immutable plan.

Each run freezes the Workflow, Agents, model/Provider/protocol metadata, Routes, effective capabilities, pricing, active limits/budgets, and input without credential fields. The asyncio scheduler marks dependency-ready nodes, overlaps independent work, resolves only active Condition branches, lets Merge wait only for activated inputs, and calls the same central model execution service used by the model center. Attempts, partial output, usage, cost, errors, and monotonically sequenced events are committed before clients observe terminal state.

Workflow SSE supports persisted history, `Last-Event-ID`, snapshot resynchronization, and reconnect. Cancellation propagates into model streams and queued work, preserves partial output, and releases central execution reservations. Retry-node, retry-descendants, and clone-from-node create derived runs instead of overwriting history. Startup marks unfinished runs interrupted; the supported local process does not continue work after the application closes.

## Context Memory And Retrieval

Project-owned chapter summaries, scene states, manual chapter/entity links, Pins, classifications, and policies form the durable memory layer. A composite retriever combines the current scene or bounded chapter fragment, names and aliases, entity state and relations, manual links, recent chapter summaries, timeline, open foreshadowing, rules/style, SQLite FTS5, and Pins. An embedding interface exists for future extensions but is disabled by default; no external vector database is required.

`ContextBuilder` resolves the Agent, direct model or Route, model window, Context Policy, and every target Provider before selecting content. It intersects policy and Provider classification scopes, ranks explainable source candidates, applies temporary exclusions, locks and priority overrides, enforces result and Token budgets, records truncation, and blocks when required context would be distorted or cross a data boundary. Rich editor HTML is parsed to visible text before indexing or prompt assembly, and historical manuscript retrieval uses summaries or explicitly capped fragments instead of loading the entire novel.

Every persisted `ContextBuild` is an immutable request/result/context snapshot with a content hash, included and excluded sources, reasons, relevance, classification, Provider boundary, Token estimates, truncations, and conflicts. A formal Context Retrieval workflow node emits this package. Agent nodes either consume one upstream package or invoke automatic context building, strip the package from ordinary input, and inject it exactly once as a System context. Retrieval writes only memory controls, the local FTS index, and Context snapshots; it never updates story records.

## Human Approval And Transactional Writeback

Human Approval nodes persist an immutable, hash-addressed value and source snapshot before moving both the node and run to `waiting_approval`. The durable Approval Request state machine enforces revision checks, expiration, idempotency keys, replacement links, and terminal states. Approval signals wake the in-process scheduler, while persisted SSE events and records remain the source of truth. Requesting changes creates a new revision Agent Attempt and approval round; old snapshots are never overwritten.

State Extraction invokes the central model execution path with a forced canonical JSON Schema, local validation, and at most one bounded repair. Entity resolution never guesses across ambiguous candidates. Proposed Change Sets translate extraction output into a fixed operation allowlist and freeze base values, target revisions, evidence, confidence, decisions, conflicts, and a hash. Conflict resolution creates a new revision and replacement approval for rebase/manual merge paths.

Database Writeback revalidates run, approval, Change Set hash/revision, cancellation state, project ownership, and every live record revision. It then creates the protective chapter version, applies only accepted operations, appends audit evidence, updates revisions, and rebuilds FTS in one SQLAlchemy transaction. Any validation, constraint, or FTS failure rolls back the complete write. The Approval workbench is a direct UI over these APIs; no frontend animation or local state can mark a run approved or write story data by itself.
