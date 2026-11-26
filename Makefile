.DEFAULT_GOAL := help
.PHONY: help setup fmt lint typecheck test check audit hooks

# While the repository has no Python files, mypy and pytest have nothing to check.
HAS_PY = git ls-files --cached --others --exclude-standard '*.py' | grep -q .

help: ## List commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup: ## Install the environment and Git hooks
	uv sync --locked
	uv run pre-commit install --install-hooks

fmt: ## Format and fix the code
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Style and common bugs
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Strict type checking
	@if $(HAS_PY); then uv run mypy; else echo "typecheck: no Python files"; fi

test: ## Tests (pytest exit code 5, no tests, is accepted)
	@uv run pytest || { code=$$?; [ $$code -eq 5 ]; }

check: lint typecheck test ## Everything that must pass before a commit

audit: ## Known vulnerabilities in dependencies
	@uv export --locked -q -o .venv/audit-requirements.txt >/dev/null && \
	uv run pip-audit --require-hashes --disable-pip -r .venv/audit-requirements.txt

hooks: ## Run hooks on all files (tracked or not)
	git ls-files -z --cached --others --exclude-standard | xargs -0 uv run pre-commit run --files
