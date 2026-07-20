.PHONY: dev test seed migrate backend frontend start package

PYTHON ?= python
PNPM ?= pnpm

dev:
	$(PYTHON) scripts/dev.py

test:
	cd backend && $(PYTHON) -m pytest
	cd frontend && $(PNPM) test -- --run
	cd frontend && $(PNPM) typecheck
	cd frontend && $(PNPM) build
	cd frontend && $(PNPM) e2e

seed:
	cd backend && $(PYTHON) -m app.cli seed

migrate:
	cd backend && $(PYTHON) -m app.cli migrate

backend:
	cd backend && $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && $(PNPM) dev --host 127.0.0.1 --port 5173

start:
	powershell -ExecutionPolicy Bypass -File scripts/start.ps1

package:
	powershell -ExecutionPolicy Bypass -File scripts/package-desktop.ps1
