# Implementation Plan

The product is implemented in eight acceptance-gated phases. Each phase updates `docs/TASKS.md`, runs backend tests, frontend tests, type checks, production build, and an end-to-end smoke test before the next phase starts.

All eight foundation phases and the confirmed V2 creation workflow have passed their acceptance gates. Version 2.2.1 adds model-window-aware Studio context budgeting, long-manuscript Map-Reduce analysis, automatic context recompression, and a bounded independently scrolling conversation rail on top of the reviewed continuation workflow; the final evidence is recorded in `FINAL_AUDIT.md`, `V2_REQUIREMENTS_ACCEPTANCE.md`, and `RELEASE_CHECKLIST.md`.

1. Project skeleton and novel foundations.
2. Multi-provider model protocols.
3. Custom HTTP API adapter.
4. Capability, routing, rate limiting, and cost controls.
5. Agent definitions and DAG workflows.
6. Novel memory and context retrieval.
7. Human approval and safe writeback.
8. Hardening, backup, restore, and release.
