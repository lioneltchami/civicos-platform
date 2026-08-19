# =============================================================================
# CivicOS — Makefile
# =============================================================================
# Provides shorthand commands for common development tasks.
# Run `make help` to see all available commands.
# =============================================================================

.DEFAULT_GOAL := help
PYTHON        := python
MANAGE        := $(PYTHON) manage.py
DC            := docker compose

# Colours for terminal output
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RESET  := \033[0m

.PHONY: help
help: ## Show this help message
	@echo "$(GREEN)CivicOS — available commands$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "%-20s %s\n", "Command", "Description"} \
	      /^[a-zA-Z_-]+:.*?##/ { printf "  $(YELLOW)%-18s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

.PHONY: up
up: ## Start all services (web, db, redis, worker, beat)
	$(DC) up

.PHONY: up-d
up-d: ## Start all services in the background
	$(DC) up -d

.PHONY: down
down: ## Stop all services
	$(DC) down

.PHONY: build
build: ## Rebuild Docker images
	$(DC) build

.PHONY: logs
logs: ## Tail logs from all services
	$(DC) logs -f

.PHONY: shell
shell: ## Open a Django shell_plus session in the web container
	$(DC) run --rm web $(MANAGE) shell_plus

.PHONY: bash
bash: ## Open a bash shell in the web container
	$(DC) run --rm web bash

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Run database migrations
	$(DC) run --rm web $(MANAGE) migrate --fake-initial

.PHONY: migrations
migrations: ## Create new migrations for changed models
	$(DC) run --rm web $(MANAGE) makemigrations

.PHONY: superuser
superuser: ## Create a superuser account
	$(DC) run --rm web $(MANAGE) bootstrap_superuser

.PHONY: dbshell
dbshell: ## Open a psql session
	$(DC) run --rm web $(MANAGE) dbshell

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

.PHONY: install
install: ## Install Python dependencies (development)
	pip install -r requirements/development.txt

.PHONY: lint
lint: ## Run ruff linter
	ruff check apps config

.PHONY: format
format: ## Format code with ruff
	ruff format apps config

.PHONY: typecheck
typecheck: ## Run mypy type checker
	mypy apps config

.PHONY: check
check: ## Run all Django system checks
	$(MANAGE) check --settings=config.settings.development

.PHONY: test
test: ## Run test suite (local virtualenv)
	pytest

.PHONY: test-docker
test-docker: ## Run test suite in Docker
	$(DC) run --rm -e DJANGO_SETTINGS_MODULE=config.settings.test web pytest

.PHONY: test-docker-app
test-docker-app: ## Run tests for a specific app in Docker: make test-docker-app APP=appointments
	$(DC) run --rm -e DJANGO_SETTINGS_MODULE=config.settings.test web pytest apps/$(APP)/

.PHONY: test-file-management
test-file-management: ## Run the local File Management runner with scoped reports
	python tools/run_file_management_tests.py

.PHONY: test-file-management-collect
test-file-management-collect: ## Validate File Management discovery without executing tests
	python tools/run_file_management_tests.py --collect-only

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	pytest --cov=apps --cov-report=term-missing --cov-report=html

.PHONY: test-fast
test-fast: ## Run tests in parallel (faster for large suites)
	pytest -n auto

.PHONY: audit
audit: ## Run pip-audit to check for vulnerable dependencies
	pip-audit -r requirements/base.txt

# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

.PHONY: messages
messages: ## Extract translatable strings and update .po files
	$(MANAGE) makemessages -l fr --ignore=.venv --ignore=node_modules
	$(MANAGE) makemessages -l en --ignore=.venv --ignore=node_modules

.PHONY: compilemessages
compilemessages: ## Compile .po files to .mo
	$(MANAGE) compilemessages

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

.PHONY: collectstatic
collectstatic: ## Collect static files to STATIC_ROOT
	$(MANAGE) collectstatic --noinput

# ---------------------------------------------------------------------------
# Wagtail
# ---------------------------------------------------------------------------

.PHONY: update-index
update-index: ## Rebuild Wagtail search index
	$(MANAGE) update_index

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove compiled Python files and caches
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -name "*.mo" -delete

.PHONY: reset-db
reset-db: ## ⚠️  Drop and recreate the database (development only)
	$(DC) run --rm web $(MANAGE) reset_db --noinput
	$(DC) run --rm web $(MANAGE) migrate
