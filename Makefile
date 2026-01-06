# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT

# Variables
PLAYBOOKS_DIR=playbooks
SCRIPTS_DIR=scripts

# Playbook paths
PROJECTS_ROOT_ACCESS=$(PLAYBOOKS_DIR)/projects/root_project_access
PROJECTS_BASE=$(PLAYBOOKS_DIR)/projects/base_projects
PROJECTS_EXTRA=$(PLAYBOOKS_DIR)/projects/extra_projects
COMMITTEES_BASE=$(PLAYBOOKS_DIR)/committees/base_committees
MAILING_LISTS=$(PLAYBOOKS_DIR)/mailing_lists/tlf_mailing_lists
V1_MEETINGS=$(PLAYBOOKS_DIR)/v1_meetings/umbrella_board_meeting

.PHONY: help setup check-env load load-projects load-committees load-mailing-lists load-meetings reset clean

# Default target
help:
	@echo "LFX v2 Mock Data Tool"
	@echo ""
	@echo "Setup:"
	@echo "  setup          - Print setup instructions"
	@echo "  check-env      - Verify required environment variables are set"
	@echo ""
	@echo "Load data:"
	@echo "  load           - Load all standard playbooks"
	@echo "  load-projects  - Load only project playbooks"
	@echo "  load-committees - Load only committee playbooks"
	@echo "  load-mailing-lists - Load mailing list playbooks"
	@echo "  load-meetings  - Load v1 meeting playbooks"
	@echo ""
	@echo "Maintenance:"
	@echo "  reset          - Wipe all data (NATS KV, OpenSearch, caches)"
	@echo "  clean          - Clean temporary files"
	@echo ""
	@echo "Quick start:"
	@echo "  eval \$$(./scripts/setup-env.sh)"
	@echo "  make load"

# Setup instructions
setup:
	@echo "Run this command to set up your environment:"
	@echo ""
	@echo "  eval \$$(./scripts/setup-env.sh)"
	@echo ""
	@echo "Then verify with: make check-env"

# Check required environment variables
check-env:
	@if [ -z "$$JWT_RSA_SECRET" ]; then \
		echo "❌ JWT_RSA_SECRET is not set"; \
		echo "Run: eval \$$(./scripts/setup-env.sh)"; \
		exit 1; \
	fi
	@if [ -z "$$NATS_URL" ]; then \
		echo "❌ NATS_URL is not set"; \
		echo "Run: eval \$$(./scripts/setup-env.sh)"; \
		exit 1; \
	fi
	@echo "✅ Environment variables are set"
	@echo "  NATS_URL: $$NATS_URL"
	@echo "  OPENFGA_STORE_ID: $${OPENFGA_STORE_ID:-<not set>}"
	@echo "  JWT_RSA_SECRET: <set>"

# Load all standard playbooks
load: check-env
	@echo "==> Loading all standard playbooks..."
	uv run lfx-v2-mockdata \
		--jwt-rsa-secret "$$JWT_RSA_SECRET" \
		-t $(PROJECTS_ROOT_ACCESS) $(PROJECTS_BASE) $(PROJECTS_EXTRA) $(COMMITTEES_BASE) $(MAILING_LISTS) $(V1_MEETINGS)

# Load only project playbooks
load-projects: check-env
	@echo "==> Loading project playbooks..."
	uv run lfx-v2-mockdata \
		--jwt-rsa-secret "$$JWT_RSA_SECRET" \
		-t $(PROJECTS_ROOT_ACCESS) $(PROJECTS_BASE) $(PROJECTS_EXTRA)

# Load only committee playbooks
load-committees: check-env
	@echo "==> Loading committee playbooks..."
	uv run lfx-v2-mockdata \
		--jwt-rsa-secret "$$JWT_RSA_SECRET" \
		-t $(COMMITTEES_BASE)

# Load mailing list playbooks
load-mailing-lists: check-env
	@echo "==> Loading mailing list playbooks..."
	uv run lfx-v2-mockdata \
		--jwt-rsa-secret "$$JWT_RSA_SECRET" \
		-t $(MAILING_LISTS)

# Load v1 meeting playbooks
load-meetings: check-env
	@echo "==> Loading v1 meeting playbooks..."
	uv run lfx-v2-mockdata \
		--jwt-rsa-secret "$$JWT_RSA_SECRET" \
		-t $(V1_MEETINGS)

# Reset all data
reset:
	@echo "==> Running data reset..."
	./$(SCRIPTS_DIR)/reset-data.sh

# Clean temporary files
clean:
	@echo "==> Cleaning temporary files..."
	@rm -rf .pytest_cache __pycache__ .mypy_cache .ruff_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Clean complete"
