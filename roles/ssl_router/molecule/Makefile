MOLECULE ?= molecule

THIS_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ROLE_DIR := $(abspath $(THIS_DIR)..)
BASE_CONFIG := molecule/shared/base.yml

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Molecule wrapper targets for the ssl_router role."
	@echo ""
	@echo "  default-podman-<action>  default scenario, podman-in-podman"
	@echo ""
	@echo "Actions: test create converge verify idempotence destroy login reset prepare check"

default-podman-%:
	cd $(ROLE_DIR) && $(MOLECULE) -c $(BASE_CONFIG) $* -s default
