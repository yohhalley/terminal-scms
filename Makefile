COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help init setup config build pull up stop down restart ps logs \
	test test-minitos test-oes migrations shell-minitos shell-oes

help:
	@printf '%s\n' \
		'Terminal SCMS commands:' \
		'  make setup          Create .env, validate, build, and start the core stack' \
		'  make up             Build and start the core stack' \
		'  make stop           Stop containers without removing them' \
		'  make down           Stop and remove containers; named volumes remain' \
		'  make ps             Show service health and status' \
		'  make logs           Follow logs from all services' \
		'  make test           Run MiniTOS and Terminal OES tests' \
		'  make migrations     Check both Django projects for migration drift' \
		'  make shell-minitos  Open a MiniTOS Django shell' \
		'  make shell-oes      Open a Terminal OES Django shell'

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
