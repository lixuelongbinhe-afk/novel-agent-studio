# Model Gateway

The gateway boundary uses `NormalizedModelRequest`, `NormalizedModelResponse`, `NormalizedMessage`, `NormalizedContentPart`, `NormalizedUsage`, `NormalizedStreamEvent`, and `NormalizedProviderError`.

Adapters satisfy `ModelProtocolAdapter` and register through `AdapterRegistry`. The registry currently contains:

- `mock`
- `openai_responses`
- `openai_chat` and `openai_compatible`
- `anthropic` and `anthropic_compatible`
- `gemini`
- `ollama`
- `generic_json_http`

The shared HTTP client disables automatic redirects and proxy environment inheritance, applies connection/read/write/pool timeouts, caps response size, attaches a request ID, and closes upstream responses on normal completion, errors, and consumer cancellation. Sensitive request headers are redacted by name and provider error bodies are truncated before normalization.

SSE and NDJSON parsing is incremental. It preserves UTF-8 decoder state across chunks, supports CR/LF boundaries and multiline SSE data, and surfaces malformed or prematurely closed streams as normalized events. Browser streaming uses a POST request with `AbortController`, so the selected Provider, model, response format, and full normalized request are used for both normal and streaming debug calls.

Credential values for the V2 desktop setup are resolved from Windows Credential Manager or an explicitly configured environment variable at call time. Provider records, presets, API responses, tests, and diagnostics never contain the secret value. Automated tests use local Mock/Fake Provider transports and never reach a real paid endpoint.

## Capability And Execution Control

Effective capabilities are composed from `manual_override`, `automatic_probe`, `official_metadata`, `model_list_api`, `imported_manifest`, and `provider_default` in that order. Provider/model enablement and current protocol configuration constrain the result. The gateway does not infer capability or tokenizer support from a model name.

Basic, standard, and advanced probes use bounded synthetic requests. They never include manuscript text or execute tools, enforce request/output/cost limits, support cancellation, and require confirmation for advanced probing.

Every ordinary and streaming request enters the same central execution service. Before adapter I/O it resolves a direct model or Route, checks required capabilities, applies explicit safe degradation, estimates context and cost, reserves per-request/project/Route budgets, waits for all matching global/project/Provider/model/Route/Workflow limit policies, and checks the Provider circuit. It records a ledger row for every attempt and always releases reservations.

Context preflight reports input, reserved output, total, window, remaining, utilization, token source, and known/unknown estimated cost. Utilization warns at 80%, strongly warns at 95%, and blocks at 100%. Token provenance is Provider actual usage, Provider estimate, explicit official tokenizer, explicit compatible tokenizer, then local approximation.

Structured output prefers native strict Schema, then JSON Object plus local validation, then prompted JSON plus safe parser extraction. One bounded repair is a separate metered invocation; failure is returned for human handling. Side-effect tools cannot be text-simulated. Unsupported System prompts, parameters, tools, or streaming produce visible warnings.

Routes support ordered fallback, lowest cost, lowest latency, healthiest, and manual-only selection. Fallback is allowed for rate limiting, timeout, connection, upstream 5xx, missing model, and unsupported capability. It is blocked for authentication, permission, invalid requests, refusal, Schema failure, cancellation, and data-boundary conflicts. Once a stream emits text, the gateway never switches and concatenates another model.

Provider health uses closed/open/half-open circuits; cancellation and refusal do not count as service failures. Retry-After is honored before exponential backoff with jitter. Unknown pricing is never converted to zero, and lowest-cost routing skips it.

## Generic JSON HTTP

`generic_json_http` maps a normalized request to a user-defined GET or POST API without evaluating code. Request templates use structured `{"$var": "name"}` placeholders, preserve JSON types, and expose only the documented normalized request values plus the adapter's bound credential. Parameter and response mappings use a restricted JSONPath subset with field and numeric/quoted-key access only.

Ordinary responses support JSON and Raw Text. Streams support SSE, NDJSON, concatenated Chunked JSON, and Raw Text. All four paths feed the same normalized response/event contracts, usage/tool mappings, error mapping, response-size limits, incremental UTF-8 decoding, and cancellation closure.

Every request passes `TargetGuard`. Public mode accepts only globally routable resolved addresses. `local_private` requires approval of the exact canonical scheme, host, and port. Resolution results are validated and the selected address is pinned into the outgoing URL while the original Host header and TLS SNI hostname are preserved. Redirects remain disabled.

Credentials are separate `CredentialReference` records containing environment-variable names only. Adapter manifests omit references and runtime values, pass a secret scan, import disabled, and require binding, approval, and a current successful test before enablement.

## Agent Workflow Integration

An Agent node does not call adapters directly. It validates the mapped input, renders the restricted Agent templates, then submits a normalized request to central model execution with project, Workflow, Route-run, capability, context, token, price, limit, and budget metadata. This preserves the same circuit, fallback, accounting, degradation, cancellation, and reservation behavior used by model-center calls.

Each model attempt links its invocation IDs back to a `NodeRunAttempt`. Streaming deltas are persisted as monotonically sequenced workflow events before being forwarded over SSE, while bounded partial text remains on the Attempt for cancellation and interruption recovery. Output text or JSON is locally validated against the frozen Agent contract before downstream activation.

Workflow run snapshots include model and Provider IDs, non-secret protocol options, Route entries, effective capabilities, pricing, limits, budgets, Agent versions, the compiled plan, and input. They intentionally omit credential environment-variable fields and values. The scheduler reads this snapshot so later edits cannot redirect or reinterpret an existing run.

## Context Assembly

Before an Agent invocation, `ContextBuilder` resolves the selected Agent target through the same direct-model or Route metadata used by central execution. It applies the smallest available target context window and intersects Context Policy classifications with every target Provider data policy. This happens before adapter I/O, so fallback cannot widen the approved data scope.

The builder emits an ordered System-context document plus a structured `ContextBuild` result. Required user task/current scene, structured memory, rules, timeline, foreshadowing, recent summaries, lexical matches, Pins, and upstream outputs compete under an explicit input budget after reserved output is deducted. Optional low-relevance items are removed first, summaries are preferred, bounded items may be truncated, and fewer neighbors are retained. If required or locked content still cannot fit, or a critical classification cannot cross the Provider boundary, execution is blocked with a persisted conflict instead of silently changing meaning.

A Workflow Context Retrieval node persists and outputs this package. An Agent node recognizes the typed package, removes it from normal mapped input, and injects the rendered context once. An Agent configured for automatic retrieval invokes the same builder only when no upstream package exists. Context snapshots include source and Provider evidence but no credentials or hidden reasoning.
