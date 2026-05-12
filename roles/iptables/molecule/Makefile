## Molecule wrapper targets for the iptables role.
##
## Naming hierarchy:
##   <scenario>-<driver>-<action>
##
## Scenarios x drivers:
##   default-podman-<action>          default scenario, podman
##
## Supported actions (forwarded verbatim to molecule):
##   test create converge verify idempotence destroy login reset prepare check
##
## Examples:
##   make help
##   make default-podman-test
##   make default-podman-converge
##   make default-podman-verify

MOLECULE ?= molecule

# Path to this Makefile -> role directory (parent of molecule/).
THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

# GIT_DIR shim: collections/ is gitignored at the repo root, which makes
# molecule misidentify the role as a broken collection. Hiding the git
# context keeps molecule in role mode.
GIT_SHIM := GIT_DIR=/dev/null

# ANSIBLE_COLLECTIONS_PATH pin. By default Ansible searches
# ~/.ansible/collections (and the venv's site-packages) for
# `kogeler.mini_pig.*`, which can hold a galaxy-installed snapshot that
# silently shadows the in-repo version molecule is supposed to be
# testing. Forcing the path makes Ansible ignore every other copy and
# load this collection from the same tree the Makefile lives in.
#
# Derivation: drop everything from `/ansible_collections/` onward in
# ROLE_DIR — what remains is the directory Ansible expects on
# ANSIBLE_COLLECTIONS_PATH. No hardcoded `../../...` chain, so the
# Makefile stays correct if the role is renamed or moved within the
# collection.
COLLECTIONS_ROOT := $(shell echo '$(ROLE_DIR)' | sed -E 's|/ansible_collections/.*||')
COLLECTIONS_ENV  := ANSIBLE_COLLECTIONS_PATH=$(COLLECTIONS_ROOT)

# Role-scoped base config. Deep-merged under each scenario's
# molecule.yml so cross-scenario defaults (deps, verifier, host_vars,
# default playbooks) live in one file. Path is relative to ROLE_DIR
# because molecule resolves --base-config relative to the cwd at
# invocation time, and we always cd $(ROLE_DIR) first.
BASE_CONFIG := molecule/shared/base.yml
MOLECULE_BASE := -c $(BASE_CONFIG)

PODMAN_ENV := $(GIT_SHIM) $(COLLECTIONS_ENV)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Molecule wrapper targets for the iptables role."
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          default scenario, podman"
	@echo ""
	@echo "Actions:"
	@echo "  test create converge verify idempotence destroy login reset prepare check"
	@echo ""
	@echo "Examples:"
	@echo "  make default-podman-test"
	@echo "  make default-podman-converge"
	@echo "  make default-podman-verify"

default-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s default
