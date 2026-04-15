.PHONY: install test test-v lint fmt check clean run dev e2e

install:            ## Install dependencies
	uv sync --extra dev

test:               ## Run tests
	uv run pytest tests/ -v

test-v:             ## Run tests with verbose output and short tracebacks
	uv run pytest tests/ -v --tb=short

lint:               ## Run ruff linter
	uv run ruff check src/ tests/

lint-fix:           ## Run ruff linter with auto-fix
	uv run ruff check --fix src/ tests/

fmt:                ## Format code with ruff
	uv run ruff format src/ tests/

check:              ## Lint + format check (CI gate)
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

clean:              ## Remove caches and build artifacts
	rm -rf .ruff_cache .pytest_cache __pycache__ dist/ build/ *.egg-info
	find src tests -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

run:                ## Start the server
	uv run superseded

dev:                ## Start the server with auto-reload
	uv run uvicorn app:app --reload

e2e:                ## Run Playwright browser tests (requires server running)
	npx playwright test

e2e-install:        ## Install Playwright browsers
	npx playwright install

all: check test e2e  ## Run check + test + e2e (full CI suite)
