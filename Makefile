# PAS Backend - developer shortcuts.
#
# `make` or `make help` lists every target. Recipes call the project virtualenv
# by path, so there is nothing to activate first.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV      ?= .venv
PYTHON    ?= $(VENV)/bin/python
PYTEST    ?= $(VENV)/bin/pytest
RUFF      ?= $(VENV)/bin/ruff
FASTAPI   ?= $(VENV)/bin/fastapi
PRECOMMIT ?= $(VENV)/bin/pre-commit
UV        ?= uv

HOST ?= 127.0.0.1
PORT ?= 8000

MIGRATIONS ?= supabase/migrations
# Postgres connection string for the Supabase pooler, e.g.
#   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
# Pass it per command (`make migrate-all DB_URL=...`) or export SUPABASE_DB_URL.
# The password is deliberately absent from .env/.env.example: it must never be
# committed and the app itself never needs it.
DB_URL ?= $(SUPABASE_DB_URL)

.PHONY: help install sync env dev run health test coverage lint format verify precommit precommit-install lock lock-check migrate migrate-all db-shell db-tables secrets js-check clean deploy

# --- help -------------------------------------------------------------------
help: ## List the available targets
	@echo "PAS Backend - available make targets"
	@echo
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Examples: make dev PORT=8012  |  make test FILE=tests/test_billing.py"
	@echo "          make coverage FILE=tests/test_customers.py"
	@echo "          make migrate FILE=$(MIGRATIONS)/20260927_customers.sql"

# --- setup ------------------------------------------------------------------
# `uv sync --locked` installs exactly what uv.lock pins (runtime + dev group)
# but leaves the project itself out, because pyproject.toml has no
# [build-system]; the second step adds the editable install back without
# re-resolving any dependency.
define install_deps
	$(UV) sync --locked
	$(UV) pip install -p $(VENV) -e . --no-deps
endef

install: ## Create .venv and install exactly what uv.lock pins (project + dev tools)
	$(UV) venv --python 3.12 --allow-existing
	$(install_deps)
	@echo "Installed. Next: 'make env' (add Supabase credentials), then 'make dev'."

sync: ## Reinstall exactly what uv.lock pins (run this after a pull)
	$(install_deps)

env: ## Create .env from .env.example when it is missing
	@if [ -f .env ]; then \
		echo ".env already exists - not touching it."; \
	else \
		cp .env.example .env; echo "Created .env - fill in your Supabase credentials."; \
	fi

# --- run --------------------------------------------------------------------
# Both targets pass no target on purpose: the CLI reads [tool.fastapi]
# entrypoint = "app.main:app" from pyproject.toml (before falling back to
# auto-discovery), so the app is declared in exactly one place - the same one
# `fastapi deploy` uses.
dev: ## Run the auto-reloading dev server (make dev PORT=8012)
	@test -x $(FASTAPI) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(FASTAPI) dev --host $(HOST) --port $(PORT)

run: ## Run the server without reload, as in production (make run PORT=8012)
	@test -x $(FASTAPI) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(FASTAPI) run --host $(HOST) --port $(PORT)

health: ## Check a running server (make health PORT=8012)
	@curl -fsS "http://$(HOST):$(PORT)/health" && echo

# --- quality ----------------------------------------------------------------
test: ## Run the test suite (make test FILE=tests/test_billing.py)
	@test -x $(PYTEST) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(PYTEST) $(FILE)

coverage: ## Run the suite and fail under 100% coverage of app/ (make coverage FILE=...)
	@test -x $(PYTEST) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(PYTEST) $(FILE) --cov=app --cov-report=term-missing --cov-fail-under=100

lint: ## Lint and auto-fix with ruff
	@test -x $(RUFF) || { echo "ruff is missing from $(VENV) - run 'make install'."; exit 1; }
	$(RUFF) check --fix .

format: ## Format the code with ruff
	@test -x $(RUFF) || { echo "ruff is missing from $(VENV) - run 'make install'."; exit 1; }
	$(RUFF) format .

precommit: ## Run every pre-commit hook over all files
	@test -x $(PRECOMMIT) || { echo "pre-commit missing - run 'make install'."; exit 1; }
	$(PRECOMMIT) run --all-files

precommit-install: ## Install the git hooks for this clone
	@test -x $(PRECOMMIT) || { echo "pre-commit missing - run 'make install'."; exit 1; }
	$(PRECOMMIT) install

verify: ## Run everything CI would: hooks, tests at 100% coverage, dependency lock
	@test -x $(PRECOMMIT) || { echo "pre-commit missing - run 'make install'."; exit 1; }
	@test -x $(PYTEST) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(PRECOMMIT) run --all-files
	$(PYTEST) --cov=app --cov-report=term-missing --cov-fail-under=100
	$(UV) lock --check
	@echo "All checks passed."

lock: ## Re-resolve and rewrite uv.lock
	$(UV) lock

lock-check: ## Fail when uv.lock is out of date
	$(UV) lock --check

# --- database ---------------------------------------------------------------
migrate: ## Apply one SQL file: make migrate FILE=supabase/migrations/name.sql
	@test -n "$(FILE)" || { echo "Usage: make migrate FILE=$(MIGRATIONS)/20260927_customers.sql"; exit 1; }
	@test -n "$(DB_URL)" || { echo "Set SUPABASE_DB_URL (or pass DB_URL=...) to your Postgres connection string."; exit 1; }
	psql "$(DB_URL)" -v ON_ERROR_STOP=1 -f "$(FILE)"

migrate-all: ## Apply every migration in supabase/migrations, in name order
	@test -n "$(DB_URL)" || { echo "Set SUPABASE_DB_URL (or pass DB_URL=...) to your Postgres connection string."; exit 1; }
	@for file in $(MIGRATIONS)/*.sql; do \
		echo "==> $$file"; \
		psql "$(DB_URL)" -v ON_ERROR_STOP=1 -f "$$file" || exit 1; \
	done
	@echo "All migrations applied."

db-shell: ## Open an interactive psql session against the database
	@test -n "$(DB_URL)" || { echo "Set SUPABASE_DB_URL (or pass DB_URL=...)."; exit 1; }
	psql "$(DB_URL)"

db-tables: ## List the tables in the public schema
	@test -n "$(DB_URL)" || { echo "Set SUPABASE_DB_URL (or pass DB_URL=...)."; exit 1; }
	psql "$(DB_URL)" -c "\dt public.*"

# --- repo-specific checks ---------------------------------------------------
# These two are also pre-commit hooks (`no-credentials`, `inline-js`); the recipes
# only exist so the same checks can be run over the whole tree by hand. The
# scripts are the single source of truth for both.
secrets: ## Fail when tracked files look like they hold credentials
	@test -x $(PYTHON) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(PYTHON) scripts/check_credentials.py

js-check: ## Syntax-check the inline JavaScript in app/static/index.html
	@test -x $(PYTHON) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(PYTHON) scripts/check_inline_js.py

# --- housekeeping -----------------------------------------------------------
clean: ## Remove caches and build artefacts (keeps the virtualenv)
	@rm -rf .pytest_cache .ruff_cache pas_backend.egg-info build dist
	@find . -path ./.venv -prune -o -name '__pycache__' -type d -print -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned caches and build artefacts."

deploy: ## Deploy to FastAPI Cloud
	@test -x $(FASTAPI) || { echo "No virtualenv at $(VENV) - run 'make install'."; exit 1; }
	$(FASTAPI) deploy
