## Molecule wrapper targets for the naive_proxy role.
##
## Naming hierarchy:
##   <scenario>-<driver>-<action>
##
## Even when a scenario currently supports only one driver, the driver name
## stays in the target so the schema is uniform.
##
## Scenarios x drivers:
##   default-podman-<action>          default scenario, podman-in-podman
##   bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman
##   gha-native-<action>              gha scenario, ansible-native (used in CI)
##   singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman
##
## Supported actions (forwarded verbatim to molecule):
##   test create converge verify idempotence destroy login reset prepare check
##
## Examples:
##   make help
##   make default-podman-test
##   make default-podman-converge
##   make default-podman-verify
##   make bookworm-podman-test
##   make gha-native-test
##   make singbox-stress-podman-converge
##   make singbox-stress-podman-verify

MOLECULE ?= molecule

# Path to this Makefile -> role directory (parent of molecule/).
THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

# GIT_DIR shim: collections/ is gitignored at the repo root, which makes
# molecule misidentify the role as a broken collection. Hiding the git
# context keeps molecule in role mode.
GIT_SHIM := GIT_DIR=/dev/null

# Role-scoped base config. Deep-merged under each scenario's
# molecule.yml so cross-scenario defaults (deps, verifier, host_vars,
# default playbooks) live in one file. Path is relative to ROLE_DIR
# because molecule resolves --base-config relative to the cwd at
# invocation time, and we always cd $(ROLE_DIR) first.
BASE_CONFIG := molecule/shared/base.yml
MOLECULE_BASE := -c $(BASE_CONFIG)

PODMAN_ENV := $(GIT_SHIM)
NATIVE_ENV :=

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Molecule wrapper targets for the naive_proxy role."
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          default scenario, podman-in-podman"
	@echo "  bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman"
	@echo "  gha-native-<action>              gha scenario, ansible-native"
	@echo "  singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman"
	@echo ""
	@echo "Actions:"
	@echo "  test create converge verify idempotence destroy login reset prepare check"
	@echo ""
	@echo "Examples:"
	@echo "  make default-podman-test"
	@echo "  make default-podman-converge"
	@echo "  make bookworm-podman-verify"
	@echo "  make gha-native-test"
	@echo "  make singbox-stress-podman-converge"
	@echo "  make singbox-stress-podman-verify"

default-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s default

bookworm-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s debian-bookworm

gha-native-%:
	cd $(ROLE_DIR) && $(NATIVE_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s gha

singbox-stress-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $(MOLECULE_BASE) $* -s singbox-stress
