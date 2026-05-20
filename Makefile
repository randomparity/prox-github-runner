SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
UV := uv
ACTIVATE := source $(VENV)/bin/activate

.PHONY: help setup lint syntax test inventory preflight check clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: $(PYTHON) ## Create venv and install development dependencies

$(PYTHON):
	$(UV) venv $(VENV) --python 3.13
	$(UV) pip install --python $(PYTHON) -r requirements-dev.txt
	@if grep -q '^  - name:' requirements.yml; then \
		$(ACTIVATE) && ansible-galaxy collection install -r requirements.yml; \
	fi

lint: setup ## Run YAML, Ansible, and Python lint
	$(ACTIVATE) && yamllint -c .yamllint.yml .
	$(ACTIVATE) && ansible-lint playbooks roles
	$(ACTIVATE) && ruff check .
	$(ACTIVATE) && ruff format --check .
	$(ACTIVATE) && ty check

syntax: setup ## Run Ansible playbook syntax checks
	set -e; $(ACTIVATE); for playbook in playbooks/*.yml; do \
		ansible-playbook --syntax-check "$$playbook"; \
	done

test: setup ## Run unit tests
	$(ACTIVATE) && pytest

inventory: setup ## Parse inventory
	$(ACTIVATE) && ansible-inventory --list >/dev/null

preflight: setup ## Run GitHub preflight
	$(ACTIVATE) && ansible-playbook playbooks/preflight.yml

check: lint syntax test inventory ## Run local verification

clean: ## Move local generated files to Trash when possible
	@if command -v trash >/dev/null 2>&1; then \
		[ ! -e "$(VENV)" ] || trash "$(VENV)"; \
		[ ! -e .pytest_cache ] || trash .pytest_cache; \
		find . -type d -name __pycache__ -prune -exec trash {} +; \
	else \
		echo "trash command not found; skipping clean to avoid destructive removal"; \
	fi
