.DEFAULT_GOAL := help
PY := .venv/bin/python
SHELL := /bin/bash

# Upstream parses sys.argv at import time unless told not to; pytest's own flags would
# otherwise be handed to it.
export MAC_CLEANUP_NO_ARGPARSE := 1

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Run the one-time privileged installer
	./install.sh

.PHONY: venv
venv: ## Create the virtualenv and install dependencies
	uv venv --python 3.13
	uv pip install rich attrs inquirer toml beartype xattr pytest pytest-cov

.PHONY: test
test: ## Run the full test suite
	$(PY) -m pytest tests/ -q --no-cov

.PHONY: coverage
coverage: ## Run tests with a coverage report
	$(PY) -m pytest tests/ --cov=mc --cov=mac_cleanup --cov-report=term-missing

.PHONY: safety
safety: ## Run only the tests that guard against data loss
	$(PY) -m pytest tests/test_mc_policy.py tests/test_mc_runtime.py tests/test_mc_quarantine.py -q --no-cov

.PHONY: lint
lint: ## Lint shell scripts, the plist and the root helper
	shellcheck -x install.sh bin/mc mc/privileged/mc-askpass
	bash -n install.sh bin/mc
	plutil -lint launchd/*.plist
	/usr/bin/python3 -m py_compile mc/privileged/mc-root mc/policy.py
	@echo "lint ok"

.PHONY: check
check: lint test ## Lint and test everything

.PHONY: dry-run
dry-run: ## Show what an aggressive run would clean, without touching anything
	./bin/mc --dry-run --verbose

.PHONY: doctor
doctor: ## Check the installation
	./bin/mc --doctor

.PHONY: policy
policy: ## Print the active path policy
	./bin/mc --explain-policy

.PHONY: modules
modules: ## List every module with its tier and availability
	./bin/mc --list-modules

.PHONY: upstream
upstream: ## Merge the latest changes from mac-cleanup-py
	git fetch upstream
	git merge upstream/main
	@echo "Now run 'make test' - test_upstream_modules_are_triaged will flag any new upstream module."

.PHONY: clean
clean: ## Remove build and test artefacts from this repo
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
