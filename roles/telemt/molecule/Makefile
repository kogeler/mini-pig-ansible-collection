## Local development and Molecule wrapper targets for the telemt role.
##
## First-time setup:
##   make bootstrap       create molecule/.venv with role-local tools
##
## Scenario targets:
##   default-podman-<action>
##   gha-native-<action>

PYTHON ?= python3

THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

VENV_DIR := $(THIS_DIR).venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_READY := $(VENV_DIR)/.ready
REQUIREMENTS := $(THIS_DIR)requirements-dev.txt

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
	@echo "  ci-podman              clean default test with live Telegram probes"
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>  direct + WEB on Debian trixie"
	@echo "  gha-native-<action>      direct + WEB on the native CI runner"
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

.PHONY: env-info lint ci-podman
env-info: $(VENV_READY)
	$(VENV_PYTHON) --version
	$(VENV_PYTHON) -m pip show ansible-core ansible-lint molecule molecule-plugins

lint: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(ANSIBLE_LINT)

ci-podman: lint
	MP_RUN_MTP_PING=1 $(MAKE) default-podman-test

default-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s default

gha-native-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s gha
