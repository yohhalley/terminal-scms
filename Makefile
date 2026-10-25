COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help init setup config build pull up stop down restart ps logs \
	test test-minitos test-oes migrations shell-minitos shell-oes \
	create-minitos-admin create-oes-admin run-simulator-runner \
		logs-simulator logs-mqtt diagnose build-simulator test-runner test-godot

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
		'  make test-godot      Run headless Godot contract and rail tests' \
		'  make test            Run MiniTOS, OES, runner, and Godot tests' \
		'  make migrations     Check both Django projects for migration drift' \
		'  make shell-minitos  Open a MiniTOS Django shell' \
		'  make shell-oes      Open a Terminal OES Django shell' \
		'  make create-minitos-admin Create a MiniTOS administrator' \
		'  make create-oes-admin     Create an OES console administrator' \
		'  make run-simulator-runner Start the restricted host-side Godot runner'

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

test-godot: build-simulator
	$(COMPOSE) --profile simulation run --rm --no-deps godot-simulator \
		godot --headless --path /app -s res://tests/unit/test_headless_sim.gd
	$(COMPOSE) --profile simulation run --rm --no-deps godot-simulator \
		godot --headless --path /app -s res://tests/unit/test_terminal_integration.gd
	$(COMPOSE) --profile simulation run --rm --no-deps godot-simulator \
		godot --headless --path /app -s res://tests/unit/test_che_rails.gd

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
