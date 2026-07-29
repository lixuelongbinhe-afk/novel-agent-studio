# Agent Notes

## Structure

- `backend/`: FastAPI, Pydantic, SQLAlchemy, Alembic, SQLite, model gateway, tests.
- `frontend/`: React, TypeScript, Vite, Tailwind, TipTap, TanStack Query, Zustand, Vitest, Playwright.
- `docs/`: architecture, data model, gateway, security, decisions, compatibility, task status.
- `scripts/`: development, production preview, migration test, and later release helpers.

## Commands

- Development: `make dev` or `.\scripts\dev.ps1`
- Migrate/seed: `make migrate`, `make seed`
- Backend checks: `cd backend && .\.venv\Scripts\python.exe -m pytest -q`; replace module with `ruff check app tests` or `mypy app`
- Frontend checks: `cd frontend && pnpm test -- --run && pnpm typecheck && pnpm build && pnpm e2e`

`mypy --strict` is enabled in CI. New backend code must have complete type annotations. Migration changes also require `python -m app.cli migrate`.

## Code Rules

- Validate API inputs with Pydantic and wrap writes in transactions.
- Keep routes, services, repositories, adapters, and workflow execution separated.
- Store only credential environment variable names; never log or export secret values.
- Use Fake/Mock providers in automated tests; never call paid APIs.
- Render model output as text unless it passes an explicit sanitizer.
- After changes, run the checks for the active phase and update `docs/TASKS.md`.

## Durable Constraints

- Do not add frameworks or dependencies to solve a scoped repair without an explicit design decision.
- Tests use only the repository Fake/Mock providers and never call paid APIs.
- Treat model output, imported documents, and user templates as untrusted data; never evaluate them as HTML, SQL, templates, or code.
- Keep `follow_redirects=False` and `trust_env=False` in gateway HTTP clients.
- Keep the `string.Formatter` prompt sandbox and the JSON template depth/node limits.
- Do not introduce `dangerouslySetInnerHTML`, `innerHTML`, or `srcdoc` into the frontend.
- Keep local tokens in request headers. Streaming uses `fetch`, not `EventSource`, and tokens never belong in query parameters.
- Keep both desktop loopback assertions as defense in depth.
- Keep Alembic history immutable and linear; every new migration points at the current head.
- Use optimistic `revision` checks for records users can edit concurrently.
- Security-semantic changes must update `docs/SECURITY.md`; architectural choices belong in `docs/DECISIONS.md`.
