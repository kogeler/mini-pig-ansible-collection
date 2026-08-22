## Local development and Molecule wrapper targets for the naive_proxy role.
##
## First-time setup:
##   make venv             create molecule/.venv with latest PyPI dependencies
##   make bootstrap        alias for venv
##
## Environment maintenance:
##   make venv-refresh     upgrade the existing venv to latest PyPI releases
##   make venv-recreate    delete/recreate only the role-local venv
##   make versions         compare binary/image pins with upstream
##   make versions-check   fail if a pin is stale or inconsistent
##
## Molecule target hierarchy:
##   <scenario>-<driver>-<action>
##
## Scenarios x drivers:
##   default-podman-<action>          default scenario, podman-in-podman
##   bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman
##   gha-native-<action>              gha scenario, ansible-native (used in CI)
##   singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman
##   anytls-stress-podman-<action>    sing-box AnyTLS stress scenario, podman-in-podman
##
## Supported actions (forwarded verbatim to molecule):
##   test create converge verify idempotence destroy login reset prepare check

PYTHON ?= python3

# Path to this Makefile -> role root.
THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

# The Python virtual environment stays below molecule/.venv and is gitignored.
VENV_DIR := $(THIS_DIR).venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_READY := $(VENV_DIR)/.ready
REQUIREMENTS := $(THIS_DIR)requirements-dev.txt
VERSION_AUDIT := $(THIS_DIR)scripts/version_audit.py
SCENARIO_PLAYBOOK := $(THIS_DIR)scripts/run_scenario_playbook.py

MOLECULE ?= $(VENV_DIR)/bin/molecule
ANSIBLE_LINT ?= $(VENV_DIR)/bin/ansible-lint

# Calling a venv binary directly does not export VIRTUAL_ENV. Set the same two
# variables activation would set so every Molecule subprocess uses the local
# Python tools. Molecule discovers ../../galaxy.yml from ROLE_DIR, installs this
# checkout through ansible-compat's native prerun, and resolves its collection
# dependencies; no custom collection path or symlink is needed.
VENV_ENV := VIRTUAL_ENV=$(VENV_DIR) PATH=$(VENV_DIR)/bin:$(PATH)

# Role-scoped base config. Path is relative to ROLE_DIR because molecule
# resolves it against the cwd and every wrapper recipe first cds there.
BASE_CONFIG := molecule/shared/base.yml
MOLECULE_BASE := -c $(BASE_CONFIG)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "naive_proxy local development"
	@echo ""
	@echo "Environment:"
	@echo "  bootstrap             create the local venv (Galaxy deps are Molecule-managed)"
	@echo "  venv                  create molecule/.venv with latest PyPI dependencies"
	@echo "  venv-refresh          upgrade packages in the existing venv"
	@echo "  venv-recreate         delete and rebuild molecule/.venv"
	@echo "  env-info              print versions from the role-local venv"
	@echo ""
	@echo "Maintenance:"
	@echo "  lint                   run ansible-lint for this role"
	@echo "  versions               show local pins and current upstream releases"
	@echo "  versions-check         fail if any maintained pin is stale"
	@echo "  ci-podman              destructive clean test of every podman scenario"
	@echo "  default-podman-benchmark           rerun official-Naive throughput only"
	@echo "  bookworm-podman-benchmark          same benchmark on Debian 12"
	@echo "  singbox-stress-podman-benchmark    rerun Naive/TUN stress only"
	@echo "  anytls-stress-podman-benchmark     rerun AnyTLS/TUN stress only"
	@echo "  default-podman-runtime-refresh     exercise opt-in image refresh only"
	@echo "  bookworm-podman-runtime-refresh    same refresh check on Debian 12"
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          default scenario, podman-in-podman"
	@echo "  bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman"
	@echo "  gha-native-<action>              gha scenario, ansible-native"
	@echo "  singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman"
	@echo "  anytls-stress-podman-<action>    sing-box AnyTLS stress scenario, podman-in-podman"
	@echo ""
	@echo "Actions:"
	@echo "  test create converge verify idempotence destroy login reset prepare check"
	@echo ""
	@echo "Development examples (instances stay alive):"
	@echo "  make default-podman-converge > /tmp/default-converge.log 2>&1"
	@echo "  make default-podman-verify > /tmp/default-verify.log 2>&1"
	@echo "  make singbox-stress-podman-converge > /tmp/singbox-converge.log 2>&1"
	@echo "  make singbox-stress-podman-verify > /tmp/singbox-verify.log 2>&1"

$(VENV_READY): $(REQUIREMENTS)
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install --upgrade --requirement $(REQUIREMENTS)
	@touch $(VENV_READY)

.PHONY: venv venv-refresh venv-recreate
venv: $(VENV_READY)

venv-refresh: $(VENV_READY)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install --upgrade --requirement $(REQUIREMENTS)
	@touch $(VENV_READY)

venv-recreate:
	@test "$(VENV_DIR)" = "$(THIS_DIR).venv"
	rm -rf -- "$(VENV_DIR)"
	$(MAKE) venv

.PHONY: bootstrap
bootstrap: venv

# Native collection discovery must not be shadowed by ambient overrides.
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

# Mirrors the podman part of the CI matrix. This intentionally runs Molecule's
# full clean lifecycle and therefore destroys each scenario instance.
ci-podman: lint versions-check
	$(MAKE) default-podman-test
	$(MAKE) bookworm-podman-test
	$(MAKE) singbox-stress-podman-test
	$(MAKE) anytls-stress-podman-test

# These playbooks intentionally reuse a live, converged instance. The helper
# asks Molecule for its native ephemeral paths and refuses incomplete or
# unconverged state; callers never have to discover
# ~/.ansible/tmp/molecule.<id> manually.
.PHONY: default-podman-benchmark bookworm-podman-benchmark
.PHONY: singbox-stress-podman-benchmark anytls-stress-podman-benchmark
.PHONY: default-podman-runtime-refresh bookworm-podman-runtime-refresh
default-podman-benchmark: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/default/molecule.yml \
		--playbook molecule/shared/benchmark.yml

bookworm-podman-benchmark: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/debian-bookworm/molecule.yml \
		--playbook molecule/shared/benchmark.yml

singbox-stress-podman-benchmark: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/singbox-stress/molecule.yml \
		--playbook molecule/shared/singbox-benchmark.yml

anytls-stress-podman-benchmark: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/anytls-stress/molecule.yml \
		--playbook molecule/shared/singbox-anytls-benchmark.yml

default-podman-runtime-refresh: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/default/molecule.yml \
		--playbook molecule/shared/utils.yml

bookworm-podman-runtime-refresh: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(VENV_PYTHON) $(SCENARIO_PLAYBOOK) \
		--base-config molecule/shared/base.yml \
		--scenario-config molecule/debian-bookworm/molecule.yml \
		--playbook molecule/shared/utils.yml

default-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s default

bookworm-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s debian-bookworm

gha-native-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s gha

singbox-stress-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s singbox-stress

anytls-stress-podman-%: $(VENV_READY) _guard
	cd $(ROLE_DIR) && $(VENV_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s anytls-stress
