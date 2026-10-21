BOOKADAPTOR_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: mine build
mine:
	sudo chown -hR -- "$${SUDO_UID:-$$(id -u)}:$${SUDO_GID:-$$(id -g)}" "$(BOOKADAPTOR_DIR)"

build:
	docker compose --project-directory "$(BOOKADAPTOR_DIR)" build artifactserver
