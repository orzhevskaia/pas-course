.PHONY: up build run demo test logs down scheduled
up:
	docker compose up -d --build db app
build:
	docker compose build app
run:
	docker compose run --rm app python -m pas.cli run
demo:
	docker compose run --rm app python -m pas.cli demo
test:
	docker compose run --rm app python -m pytest -q
logs:
	docker compose logs --tail=100
scheduled:
	docker compose --profile scheduled up -d scheduler
down:
	docker compose down
