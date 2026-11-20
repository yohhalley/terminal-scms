COMPOSE ?= docker compose
SCENARIO ?= import_v1
STEP ?=
SEED ?= 1
CONTAINERS ?= 1
SIM ?= 0
SITE ?= test-terminal
TIME_SCALE ?= 1.0
# QA M2: run marker for make scenario-reset (generated per run unless given).
RESET_EPOCH ?=
# Path to the Godot 4.2.1 headless binary. CI sets this via .github/actions/setup-godot.
GODOT_BIN ?=

.DEFAULT_GOAL := help
.PHONY: help init setup config build pull up stop down restart ps logs \
	test test-minitos test-oes migrations shell-minitos shell-oes \
	create-minitos-admin create-oes-admin run-simulator-runner \
		logs-simulator logs-mqtt diagnose build-simulator test-runner test-godot \
	scenario-reset scenario-step scenario-dump \
	up-runner logs-runner sim-start sim-stop

help:
	@printf '%s\n' \
		'Terminal SCMS commands:' \
		'  make setup          Create .env, validate, build, and start the core stack' \
		'  make up             Build and start the core stack' \
		'  make stop           Stop containers without removing them' \
		'  make down           Stop and remove containers; named volumes remain' \
		'  make ps             Show service health and status' \
		'  make logs           Follow logs from all services' \
		'  make logs-mqtt      Follow HiveMQ and MQTT worker logs' \
		'  make diagnose       Show status and recent startup logs' \
		'  make logs-simulator Follow the latest on-demand Godot simulator log' \
		'  make build-simulator Build the on-demand Godot image without starting it' \
		'  make test-runner     Run restricted host-runner unit tests' \
		'  make test-godot      Run headless Godot suites as CI does (fast profile; GODOT_BIN or Docker)' \
		'  make test            Run MiniTOS, OES, runner, and Godot tests' \
		'  make migrations     Check both Django projects for migration drift' \
		'  make shell-minitos  Open a MiniTOS Django shell' \
		'  make shell-oes      Open a Terminal OES Django shell' \
		'  make create-minitos-admin Create a MiniTOS administrator' \
		'  make create-oes-admin     Create an OES console administrator' \
		'  make run-simulator-runner Start the restricted host-side Godot runner' \
		'  make up-runner      Build and start the simulator-runner service (simulation profile)' \
		'  make logs-runner    Follow the simulator-runner service log (runner + Godot)' \
		'  make sim-start [SITE=test-terminal] [TIME_SCALE=1.0]' \
		'                      Start a simulation session; the runner service launches Godot' \
		'  make sim-stop       Stop every active simulation session and wait for the runner' \
		'  make scenario-reset [SCENARIO=import_v1] [SEED=n] [CONTAINERS=1-5] [SIM=1]' \
		'                      Wipe OES + MiniTOS data, clear retained MQTT, seed and publish the Site' \
		'  make scenario-step STEP=visit-in-operation|release-discharge|dispatch-next [ORDER=id]' \
		'                      Run one scripted demo step (MiniTOS visit/release, OES dispatch)' \
		'  make scenario-dump  Print canonical-state checksums of both databases'

init:
	@test -f .env || cp .env.example .env

setup: init config
	$(COMPOSE) up --build --detach --wait
	$(COMPOSE) ps

config: init
	$(COMPOSE) config --quiet

build: init
	$(COMPOSE) build

pull: init
	$(COMPOSE) pull --ignore-buildable

up: init
	$(COMPOSE) up --build --detach --wait

stop:
	$(COMPOSE) stop

down:
	$(COMPOSE) down

restart: down up

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --follow --tail=200

logs-mqtt:
	$(COMPOSE) logs --follow --tail=200 \
		hivemq minitos-uns-consumer minitos-outbox oes-uns-consumer oes-outbox

diagnose: config
	@printf '%s\n' '=== Container status ==='
	-$(COMPOSE) ps --all
	@printf '%s\n' '=== HiveMQ and MQTT worker logs ==='
	-$(COMPOSE) logs --timestamps --tail=200 \
		hivemq minitos-uns-consumer minitos-outbox oes-uns-consumer oes-outbox

logs-simulator:
	@name=$$(docker ps --latest --filter label=scms.simulation-session \
		--format '{{.Names}}'); \
	if [ -z "$$name" ]; then echo 'No running simulation container.'; exit 1; fi; \
	docker logs --follow --tail=200 "$$name"

build-simulator: init
	$(COMPOSE) --profile simulation build godot-simulator

test-runner:
	python3 -m unittest discover -s apps/simulator-runner -p 'test_*.py'

# Same suites and invocation as the `godot` CI job (.github/workflows/ci.yml).
# Pins SIMULATOR_TIMING_PROFILE=fast so a realistic value from the environment,
# .env.example, or compose.yaml cannot leak in. Does not change the runtime
# default in those files. run-godot-test.sh fails on a non-zero exit, a
# timeout, or a SCRIPT ERROR (Godot can exit 0 after one).
# If GODOT_BIN is set, use that host binary the way CI does, with a throwaway
# HOME so user:// state from other Godot runs on this machine cannot leak in.
# If GODOT_BIN is unset, build the godot-simulator image and run the same
# suites inside that container, so a local Godot install is not required.
# The profile is passed with -e so compose.yaml:207 and .env cannot override
# it. Scripts are mounted at /ci-scripts and run with bash (bookworm-slim).
GODOT_TEST_SUITES := \
	tests/unit/test_headless_sim.gd \
	tests/unit/test_terminal_integration.gd \
	tests/unit/test_che_rails.gd \
	tests/unit/test_cancel_regression.gd \
	tests/requires-TCL-014a/test_acceptance_d012_d013.gd

# Two recipes, not one shell if with a recursive make. A recipe line that
# itself calls make is executed even under make -n.
ifeq ($(strip $(GODOT_BIN)),)
test-godot: build-simulator
	$(COMPOSE) --profile simulation run --rm --no-deps -T \
		-e SIMULATOR_TIMING_PROFILE=fast \
		-e GODOT_BIN=godot \
		-v "$(CURDIR)/.github/scripts:/ci-scripts:ro" \
		--entrypoint bash \
		godot-simulator \
		-c "set -e; export HOME=\$$(mktemp -d); /ci-scripts/godot-import.sh /app; $(foreach s,$(GODOT_TEST_SUITES),/ci-scripts/run-godot-test.sh 600 $(s); )"
else
test-godot:
	@set -e; \
	workdir=$$(mktemp -d); \
	trap 'rm -rf "$$workdir"' EXIT; \
	export HOME="$$workdir"; \
	export GODOT_BIN="$(GODOT_BIN)"; \
	export SIMULATOR_TIMING_PROFILE=fast; \
	cd clients/godot-open-terminal; \
	../../.github/scripts/godot-import.sh .; \
	for script in $(GODOT_TEST_SUITES); do \
		../../.github/scripts/run-godot-test.sh 600 "$$script"; \
	done
endif

run-simulator-runner: init build-simulator
	set -a; . ./.env; set +a; python3 apps/simulator-runner/runner.py

test: test-minitos test-oes test-runner test-godot

test-minitos: init
	$(COMPOSE) run --rm minitos python manage.py test --settings=minitos_site.settings_test

test-oes: init
	$(COMPOSE) run --rm terminal-oes python manage.py test

migrations: init
	$(COMPOSE) run --rm minitos python manage.py makemigrations --check --dry-run
	$(COMPOSE) run --rm terminal-oes python manage.py makemigrations --check --dry-run

shell-minitos: init
	$(COMPOSE) run --rm minitos python manage.py shell

shell-oes: init
	$(COMPOSE) run --rm terminal-oes python manage.py shell

create-minitos-admin: init
	$(COMPOSE) exec minitos python manage.py createsuperuser

create-oes-admin: init
	$(COMPOSE) exec terminal-oes python manage.py createsuperuser

# TCL-006: reproducible demo scenarios. Each service resets its own database
# through its own management command (no cross-database writes). Order matters:
# OES first (it must forget the old Site revision), then retained MQTT, then
# MiniTOS seeds and publishes through its outbox, then we wait for OES to project.
# SEED (default 1, spec K7) is one knob for both seeds: terminal-oes is recreated
# with SIMULATOR_SEED=$(SEED) when it differs (a no-op otherwise), so the next
# simulator session gets the same seed as the MiniTOS scenario RNG.
# QA M2: both resets get the same fresh RESET_EPOCH. Each service stamps it on
# the envelopes it publishes and ignores envelopes from any other epoch, so a
# message from the previous run (queued for the persistent OES session, or an
# old MiniTOS outbox row published between the two wipes) is not accepted.
SCENARIO_ORDER_ARG = $(if $(ORDER),--order $(ORDER),)
# A failed service reset leaves the two services on different runs: stop with a
# non-zero exit and say so, instead of carrying on to scenario_wait_ready.
SCENARIO_RESET_FAILED = { echo "scenario-reset: $(1) reset failed; the services are not on the same run. Fix the error above and re-run scenario-reset." >&2; exit 1; }
ifeq ($(strip $(RESET_EPOCH)),)
SCENARIO_RESET_EPOCH := $(shell date -u +%Y%m%dT%H%M%SZ)-$(shell od -An -N4 -tx1 /dev/urandom | tr -d ' \n')
else
SCENARIO_RESET_EPOCH := $(strip $(RESET_EPOCH))
endif

# SIM=1 (Runner, S2): stop the running simulation first and start a fresh session
# at the end, so the simulator-runner service plays the reseeded scenario.
scenario-reset: init
	@echo "scenario-reset: reset epoch $(SCENARIO_RESET_EPOCH)"
	SIMULATOR_SEED=$(SEED) $(COMPOSE) up --detach --no-deps --wait terminal-oes
	@if [ '$(SIM)' = '1' ]; then $(COMPOSE) exec -T terminal-oes python manage.py simulation_stop --wait 30; fi
	$(COMPOSE) exec -T terminal-oes python manage.py scenario_reset $(SCENARIO) --noinput --seed $(SEED) \
		--reset-epoch $(SCENARIO_RESET_EPOCH) || $(call SCENARIO_RESET_FAILED,terminal-oes)
	$(COMPOSE) exec -T minitos python manage.py clear_retained_mqtt
	$(COMPOSE) exec -T minitos python manage.py scenario_reset $(SCENARIO) --noinput \
		--seed $(SEED) --containers $(CONTAINERS) --reset-epoch $(SCENARIO_RESET_EPOCH) \
		|| $(call SCENARIO_RESET_FAILED,minitos)
	$(COMPOSE) exec -T terminal-oes python manage.py scenario_wait_ready --timeout 60
	@if [ '$(SIM)' = '1' ]; then $(COMPOSE) exec -T terminal-oes python manage.py simulation_start \
		--site $(SITE) --time-scale $(TIME_SCALE); fi

# Runner (S2): the simulator-runner compose service (profile "simulation").
up-runner: init
	$(COMPOSE) --profile simulation up --build --detach --wait simulator-runner

logs-runner:
	$(COMPOSE) --profile simulation logs -f --tail=200 simulator-runner

sim-start: init
	$(COMPOSE) exec -T terminal-oes python manage.py simulation_start --site $(SITE) --time-scale $(TIME_SCALE)

sim-stop: init
	$(COMPOSE) exec -T terminal-oes python manage.py simulation_stop --wait 30

scenario-step: init
	@case '$(STEP)' in \
	visit-in-operation|release-discharge) \
		$(COMPOSE) exec -T minitos python manage.py scenario_step $(SCENARIO) $(STEP) $(SCENARIO_ORDER_ARG) ;; \
	dispatch-next) \
		$(COMPOSE) exec -T terminal-oes python manage.py scenario_step $(SCENARIO) $(STEP) $(SCENARIO_ORDER_ARG) ;; \
	*) echo 'Usage: make scenario-step STEP=visit-in-operation|release-discharge|dispatch-next [ORDER=id]'; exit 2 ;; \
	esac

scenario-dump: init
	@printf 'minitos      '; $(COMPOSE) exec -T minitos python manage.py scenario_dump --checksum
	@printf 'terminal-oes '; $(COMPOSE) exec -T terminal-oes python manage.py scenario_dump --checksum
