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
##   default-vagrant-<action>         default scenario, vagrant-libvirt
##   bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman
##   gha-native-<action>              gha scenario, ansible-native (used in CI)
##   singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman
##   singbox-stress-vagrant-<action>  sing-box stress scenario, vagrant-libvirt
##
## Supported actions (forwarded verbatim to molecule):
##   test create converge verify idempotence destroy login reset prepare check
##
## Examples:
##   make help
##   make default-podman-test
##   make default-vagrant-converge
##   make default-vagrant-verify
##   make bookworm-podman-test
##   make gha-native-test
##   make singbox-stress-podman-converge
##   make singbox-stress-podman-verify

MOLECULE ?= molecule
PY       ?= python

# Path to this Makefile -> role directory (parent of molecule/).
THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)

# molecule 26 no longer wires driver.modules_dir() into ANSIBLE_LIBRARY
# (see molecule-plugins#301), so we resolve the vagrant plugin modules
# directory here and inject it explicitly. Empty if the plugin is missing;
# the _check-vagrant target turns that into a helpful error.
VAGRANT_LIB := $(shell $(PY) -c 'import os, molecule_plugins.vagrant as m; print(os.path.join(os.path.dirname(m.__file__), "modules"))' 2>/dev/null)

# GIT_DIR shim: collections/ is gitignored at the repo root, which makes
# molecule misidentify the role as a broken collection. Hiding the git
# context keeps molecule in role mode.
GIT_SHIM := GIT_DIR=/dev/null

PODMAN_ENV  := $(GIT_SHIM) MP_DRIVER=podman
VAGRANT_ENV := $(GIT_SHIM) MP_DRIVER=vagrant ANSIBLE_LIBRARY=$(VAGRANT_LIB)
NATIVE_ENV  :=

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Molecule wrapper targets for the naive_proxy role."
	@echo ""
	@echo "Scenarios x drivers:"
	@echo "  default-podman-<action>          default scenario, podman-in-podman"
	@echo "  default-vagrant-<action>         default scenario, vagrant-libvirt"
	@echo "  bookworm-podman-<action>         debian-bookworm scenario, podman-in-podman"
	@echo "  gha-native-<action>              gha scenario, ansible-native"
	@echo "  singbox-stress-podman-<action>   sing-box stress scenario, podman-in-podman"
	@echo "  singbox-stress-vagrant-<action>  sing-box stress scenario, vagrant-libvirt"
	@echo ""
	@echo "Actions:"
	@echo "  test create converge verify idempotence destroy login reset prepare check"
	@echo ""
	@echo "Examples:"
	@echo "  make default-podman-test"
	@echo "  make default-vagrant-converge"
	@echo "  make bookworm-podman-verify"
	@echo "  make gha-native-test"
	@echo "  make singbox-stress-podman-converge"
	@echo "  make singbox-stress-podman-verify"

.PHONY: _check-vagrant
_check-vagrant:
	@test -n "$(VAGRANT_LIB)" || { \
	    echo "ERROR: molecule_plugins.vagrant not importable from $(PY)." >&2; \
	    echo "Install it with: pip install 'molecule-plugins[vagrant]' python-vagrant" >&2; \
	    exit 1; \
	}

default-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $* -s default

default-vagrant-%: _check-vagrant
	cd $(ROLE_DIR) && $(VAGRANT_ENV) $(MOLECULE) $* -s default

bookworm-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $* -s debian-bookworm

gha-native-%:
	cd $(ROLE_DIR) && $(NATIVE_ENV) $(MOLECULE) $* -s gha

singbox-stress-podman-%:
	cd $(ROLE_DIR) && $(PODMAN_ENV) $(MOLECULE) $* -s singbox-stress

singbox-stress-vagrant-%: _check-vagrant
	cd $(ROLE_DIR) && $(VAGRANT_ENV) $(MOLECULE) $* -s singbox-stress
