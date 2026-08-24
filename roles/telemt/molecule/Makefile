## Local development and Molecule wrapper targets for the telemt role.
##
## First-time setup:
##   make bootstrap       create molecule/.venv with role-local tools
##
## Scenario targets:
##   default-podman-<action>
##   web-podman-<action>
##   web-lanes-podman-<action>
##   web-bookworm-podman-<action>
##   gha-native-<action>
##   gha-web-native-<action>

PYTHON ?= python3

THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

VENV_DIR := $(THIS_DIR).venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_READY := $(VENV_DIR)/.ready
REQUIREMENTS := $(THIS_DIR)requirements-dev.txt
VERSION_AUDIT := $(THIS_DIR)scripts/version_audit.py
SCENARIO_PLAYBOOK := $(THIS_DIR)scripts/run_scenario_playbook.py

MOLECULE ?= $(VENV_DIR)/bin/molecule
ANSIBLE_LINT ?= $(VENV_DIR)/bin/ansible-lint
VENV_ENV := VIRTUAL_ENV=$(VENV_DIR) PATH=$(VENV_DIR)/bin:$(PATH)

BASE_CONFIG := molecule/shared/base.yml
MOLECULE_BASE := -c $(BASE_CONFIG)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "telemt local development"
	@echo ""
	@echo "Environment:"
	@echo "  bootstrap             create molecule/.venv"
	@echo "  venv                  create molecule/.venv"
	@echo "  venv-refresh          upgrade role-local Python tools"
	@echo "  venv-recreate         delete and rebuild only molecule/.venv"
	@echo "  env-info              show effective tool versions"
	@echo ""
	@echo "Maintenance:"
	@echo "  lint                   run ansible-lint for this role"
	@echo "  versions               compare runtime pins with upstream"
	@echo "  versions-check         fail when a maintained pin is stale"
	@echo "  ci-podman              clean test of every Podman scenario"
	@echo "  web-podman-transition  run mode-transition checks on a live web instance"
	@echo "  web-lanes-podman-e2e   run browser/Telegram E2E on a live web-lanes instance"
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          direct/FakeTLS regression"
	@echo "  web-podman-<action>              WEB https + HAProxy"
	@echo "  web-lanes-podman-<action>        WEB https-lanes/browser E2E"
	@echo "  web-bookworm-podman-<action>     WEB on Debian 12"
	@echo "  gha-native-<action>              direct native CI runner"
	@echo "  gha-web-native-<action>          WEB native CI runner"
	@echo ""
	@echo "Actions: test create converge verify idempotence destroy login reset prepare check"

$(VENV_READY): $(REQUIREMENTS)
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install --upgrade --requirement $(REQUIREMENTS)
	@touch $(VENV_READY)

.PHONY: venv bootstrap venv-refresh venv-recreate
venv: $(VENV_READY)

bootstrap: venv

venv-refresh: $(VENV_READY)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install --upgrade --requirement $(REQUIREMENTS)
	@touch $(VENV_READY)

venv-recreate:
	@test "$(VENV_DIR)" = "$(THIS_DIR).venv"
	rm -rf -- "$(VENV_DIR)"
	$(MAKE) venv

.PHONY: _guard
_guard:
	@if [ -n "$$ANSIBLE_COLLECTIONS_PATH" ]; then \
		echo "ERROR: unset ANSIBLE_COLLECTIONS_PATH; Molecule manages collection discovery."; \
		exit 1; \
	fi
	@if [ -n "$$GIT_DIR" ]; then \
		echo "ERROR: unset GIT_DIR; ansible-compat needs the real checkout metadata."; \
		exit 1; \
	fi

.PHONY: env-info lint versions versions-check ci-podman
env-info: $(VENV_READY)
	$(VENV_PYTHON) --version
	$(VENV_PYTHON) -m pip show ansible-core ansible-lint molecule molecule-plugins

lint: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(ANSIBLE_LINT)

versions: $(VENV_READY)
	$(VENV_PYTHON) $(VERSION_AUDIT)

versions-check: $(VENV_READY)
	$(VENV_PYTHON) $(VERSION_AUDIT) --check

ci-podman: lint versions-check
	$(MAKE) default-podman-test
	$(MAKE) web-podman-test
	$(MAKE) web-lanes-podman-test
	$(MAKE) web-bookworm-podman-test

.PHONY: web-podman-transition web-lanes-podman-e2e
web-podman-transition: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/web/molecule.yml \
		--playbook molecule/shared/transition.yml

web-lanes-podman-e2e: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/web-lanes/molecule.yml \
		--playbook molecule/shared/web-e2e.yml

default-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s default

web-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s web

web-lanes-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s web-lanes

web-bookworm-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s web-bookworm

gha-native-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s gha

gha-web-native-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s gha-web
