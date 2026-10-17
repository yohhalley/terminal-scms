COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help init setup config build pull up stop down restart ps logs \
	test test-minitos test-oes migrations shell-minitos shell-oes \
	create-minitos-admin create-oes-admin bootstrap-simulator-user \
	logs-simulator logs-mqtt diagnose

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
		'  make logs-simulator Follow the headless Godot simulator log' \
		'  make test           Run MiniTOS and Terminal OES tests' \
		'  make migrations     Check both Django projects for migration drift' \
		'  make shell-minitos  Open a MiniTOS Django shell' \
		'  make shell-oes      Open a Terminal OES Django shell' \
		'  make create-minitos-admin Create a MiniTOS administrator' \
		'  make create-oes-admin     Create an OES console administrator' \
		'  make bootstrap-simulator-user Reconcile the Godot MiniTOS account'

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
	$(COMPOSE) logs --follow --tail=200 godot-simulator

test: test-minitos test-oes

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

bootstrap-simulator-user: init
	$(COMPOSE) exec minitos python manage.py ensure_simulator_user
