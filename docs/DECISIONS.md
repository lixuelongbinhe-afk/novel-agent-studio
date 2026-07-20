# Decisions

## Phase 1

- SQLite is the default database for local-first use.
- Chapter autosave uses optimistic concurrency with explicit revision checks.
- Restore creates a protective version of the current chapter before applying old content.
- The model center starts with mock provider infrastructure and data records; real protocols are phase 2.

## Phase 2

- Protocol-specific serialization stays behind one normalized gateway and one shared bounded `httpx.AsyncClient`.
- Automated compatibility uses deterministic local Fake Providers; paid endpoints are never part of the test suite.
- Provider credentials remain environment-variable references resolved only at request time.

## Phase 3

- Custom request templates are typed JSON trees with exact `$var` nodes. String interpolation and executable template languages are not accepted.
- Response mapping uses a deliberately small JSONPath subset rather than a script-capable implementation.
- SSRF defense validates all DNS answers and pins the request to a validated address while retaining Host and SNI.
- Local network access is an explicit exact-Origin approval state that is fingerprinted and revoked after security-relevant changes.
- Provider and custom-adapter creation is one backend transaction so a failed adapter cannot leave an orphan Provider.
- Manifests are portable configuration only: credential bindings, approval, test state, and enablement never transfer.
- An unversioned legacy database is classified by complete phase table sets, stamped only at the last schema it can prove, and then upgraded normally. Partial schemas fail without writing an Alembic success marker.
- Programmatic migration targets are passed explicitly into Alembic; `env.py` must not silently replace a caller-selected database URL with the default database.

## Phase 4

- Capability truth is data-driven with a fixed source priority. Provider and model state constrain the result, and a model-name pattern is never evidence of support.
- Ordinary and streaming requests share one central execution service so UI, future Agents, retries, budgets, health, and ledger accounting cannot drift into separate paths.
- Tokenizer use is explicit configuration. A compatible tokenizer may be selected deliberately, while an unconfigured model uses a clearly marked local approximation.
- Unknown price is a first-class state. Cost-based routing and cost budgets must block or skip unknown candidates instead of treating them as free.
- Rate and budget reservations are atomic in the supported single-process deployment. Replacing them with a distributed coordinator is deferred unless the deployment model changes.
- Fallback is governed by normalized error categories, project boundaries, capability checks, and partial-stream state. Retryable does not mean universally safe to switch.
- Strict structured streams may buffer for local validation and one bounded repair. Correctness and non-duplicated output take precedence over pretending that repaired JSON was natively streamed.
- Content refusal and user cancellation are neutral to Provider health. Transport/service failures drive closed/open/half-open circuit transitions.

## Phase 5

- Agent configuration is immutable by version at execution time. Renaming or toggling an Agent does not create a semantic version; prompt, target, Schema, parameter, capability, retry, timeout, or budget changes do.
- Workflow drafts and executable plans are separate concepts. Runs compile and freeze a validated plan plus all non-secret dependencies so later edits cannot change historical behavior.
- DAG readiness is driven by resolved active edges, not merely predecessor completion. This lets Condition skip one branch and lets Merge wait only for branches that were actually activated.
- Every retry is a separate `NodeRunAttempt`. Partial outputs and model invocation IDs remain attached to their attempt; no retry overwrites evidence from an earlier failure.
- Run correction creates a parent-linked derived run for node retry, descendant retry, or clone-from-node. Historical runs remain immutable and auditable.
- Workflow events are persisted before delivery and use a per-run monotonic sequence. SSE is a transport over durable state, not the source of truth.
- The supported scheduler is in-process asyncio. Startup marks orphaned work interrupted and offers derivation; adding a distributed queue is deferred because the supported deployment remains one local backend process.
- Portable workflow manifests exclude credentials and enablement. Import creates disabled definitions, resolves Agent references explicitly, and respects each destination model's field length constraints.

## Phase 6

- SQLite FTS5 plus structured relational retrievers is the default local baseline. An embedding protocol is reserved but disabled so the product does not require a network service or external vector database.
- Context selection is explainable data, not an opaque prompt concatenation. Every candidate retains source identity, relevance, reasons, classification, priority, Token estimate, inclusion state, truncation, and Provider-boundary outcome.
- The entire novel is never injected by default. The current scene or a capped current-chapter fragment may be required; older chapters use summaries when available and otherwise use bounded fragments.
- A Route's effective data scope is the intersection across all candidate Providers. This is intentionally conservative because fallback must not send content that only the first Provider was allowed to receive.
- Required or locked content that cannot fit or cannot cross the target data boundary blocks the build. Quietly omitting critical facts is treated as semantic corruption, not successful degradation.
- Persisted Context Builds are immutable hash-addressed snapshots. Rebuilding after a source edit creates new evidence; reading an old build never re-runs retrieval against current data.
- A Context Retrieval node creates a typed package. Agent nodes consume that package once, or use automatic building when configured, but never concatenate both paths or leak the package into the ordinary user prompt.
- Retrieval and preview are read-only with respect to story state. Human approval, proposed changes, extraction, and transactional writeback remain a separate Phase 7 boundary.

## Phase 7

- Approval is a persisted immutable snapshot state machine, not a transient modal result. The scheduler pauses on durable state and resumes the same run only after a valid current decision.
- Editing or requesting changes supersedes the old approval and creates a new snapshot/revision round. Historical model output and Attempts remain immutable, and automatic revision is capped at three rounds.
- Structured extraction is untrusted input even when a Provider advertises strict Schema support. Local validation is mandatory and only one bounded repair is allowed.
- Entity identity favors false negatives over false merges. Ambiguity must reach the user; string similarity alone never authorizes a merge.
- Proposed Change Sets are the only bridge from model output to story mutation. Their operation and field allowlists deliberately exclude arbitrary table/column selection and SQL.
- Conflict resolution changes the proposal, so rebase and manual merge require a replacement approval. A previously approved hash cannot authorize a different set of writes.
- Version creation, accepted writes, audit append, revisions, and FTS refresh are one transaction. Audit completeness takes precedence over partial progress.
