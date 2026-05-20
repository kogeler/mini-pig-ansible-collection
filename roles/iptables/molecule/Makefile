## Molecule wrapper targets for the iptables role.
##
## Naming hierarchy:
##   <scenario>-<driver>-<action>
##
## Scenarios x drivers:
##   default-podman-<action>          default scenario, podman
##   scoped-migration-podman-<action> iptables-persistent → scoped iptables migration
##   nftables-podman-<action>         nftables scenario (incl. iptables→nft migration), podman
##
## Supported actions (forwarded verbatim to molecule):
##   test create converge verify idempotence destroy login reset prepare check
##
## Examples:
##   make help
##   make default-podman-test
##   make default-podman-converge
##   make default-podman-verify
##   make scoped-migration-podman-test
##   make scoped-migration-podman-converge
##   make scoped-migration-podman-verify
##   make nftables-podman-test
##   make nftables-podman-converge
##   make nftables-podman-verify

MOLECULE ?= molecule

# Path to this Makefile -> role directory (parent of molecule/).
THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

# Role-scoped base config. Deep-merged under each scenario's
# molecule.yml so cross-scenario defaults (deps, verifier, host_vars,
# default playbooks) live in one file. Path is relative to ROLE_DIR
# because molecule resolves --base-config relative to the cwd at
# invocation time, and we always cd $(ROLE_DIR) first.
BASE_CONFIG := molecule/shared/base.yml
MOLECULE_BASE := -c $(BASE_CONFIG)

# -- Regression guard ---------------------------------------------------------
#
# DO NOT remove `_guard` or weaken these env-var checks. They exist
# because in a previous round somebody (an agent — me) pinned
# ANSIBLE_COLLECTIONS_PATH and GIT_DIR=/dev/null in this Makefile to
# "fix" a Molecule discovery issue. The "fix" silently caused Molecule
# to test a stale collection cached under that override path instead of
# the in-tree role — green CI, broken code in production. See
# roles/iptables/AGENTS.md pitfall P5 for the doc-backed reasoning.
#
# If you find yourself wanting to set ANSIBLE_COLLECTIONS_PATH or
# GIT_DIR here as a workaround, STOP. The canonical workflow is
# `cd $(ROLE_DIR) && molecule …` — ansible-compat auto-discovers the
# collection from galaxy.yml. If discovery is failing, fix the
# invocation, not the environment.

.DEFAULT_GOAL := help

.PHONY: _guard
_guard:
	@if [ -n "$$ANSIBLE_COLLECTIONS_PATH" ]; then \
	    echo ""; \
	    echo "ERROR: ANSIBLE_COLLECTIONS_PATH is set in the environment."; \
	    echo ""; \
	    echo "This breaks ansible-compat's collection auto-discovery from"; \
	    echo "galaxy.yml and causes Molecule to test a stale checkout from"; \
	    echo "the override path instead of the in-tree role."; \
	    echo "Unset it and retry. See roles/iptables/AGENTS.md pitfall P5."; \
	    echo ""; \
	    exit 1; \
	fi
	@if [ -n "$$GIT_DIR" ]; then \
	    echo ""; \
	    echo "ERROR: GIT_DIR is set in the environment."; \
	    echo ""; \
	    echo "This masks the git state ansible-compat reads when locating"; \
	    echo "galaxy.yml. Unset it. See roles/iptables/AGENTS.md pitfall P5."; \
	    echo ""; \
	    exit 1; \
	fi

.PHONY: help
help:
	@echo "Molecule wrapper targets for the iptables role."
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          default scenario, podman"
	@echo "  scoped-migration-podman-<action> iptables-persistent → scoped iptables migration"
	@echo "  nftables-podman-<action>         nftables scenario (incl. migration), podman"
	@echo ""
	@echo "Actions:"
	@echo "  test create converge verify idempotence destroy login reset prepare check"
	@echo ""
	@echo "Examples:"
	@echo "  make default-podman-test"
	@echo "  make default-podman-converge"
	@echo "  make default-podman-verify"
	@echo "  make scoped-migration-podman-test"
	@echo "  make scoped-migration-podman-converge"
	@echo "  make scoped-migration-podman-verify"
	@echo "  make nftables-podman-test"
	@echo "  make nftables-podman-converge"
	@echo "  make nftables-podman-verify"

default-podman-%: _guard
	cd $(ROLE_DIR) && $(MOLECULE) $(MOLECULE_BASE) $* -s default

scoped-migration-podman-%: _guard
	cd $(ROLE_DIR) && $(MOLECULE) $(MOLECULE_BASE) $* -s scoped_migration

nftables-podman-%: _guard
	cd $(ROLE_DIR) && $(MOLECULE) $(MOLECULE_BASE) $* -s nftables
