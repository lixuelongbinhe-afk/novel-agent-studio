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

## Code Rules

- Validate API inputs with Pydantic and wrap writes in transactions.
- Keep routes, services, repositories, adapters, and workflow execution separated.
- Store only credential environment variable names; never log or export secret values.
- Use Fake/Mock providers in automated tests; never call paid APIs.
- Render model output as text unless it passes an explicit sanitizer.
- After changes, run the checks for the active phase and update `docs/TASKS.md`.
