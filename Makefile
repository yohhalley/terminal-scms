.PHONY: config up down logs ps test

config:
	docker compose config --quiet

up:
	docker compose up --build --detach

down:
	docker compose down

logs:
	docker compose logs --follow

ps:
	docker compose ps

test:
	docker compose run --rm minitos python manage.py test --settings=minitos_site.settings_test
	docker compose run --rm terminal-oes python manage.py test

