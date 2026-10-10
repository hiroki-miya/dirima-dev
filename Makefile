.PHONY: up down migrate test e2e lint typecheck
up:
	docker compose up --build -d
down:
	docker compose down
migrate:
	docker compose run --rm migrate
test:
	cd backend && uv run --extra dev pytest
	cd frontend && npm run test
e2e:
	cd frontend && npm run test:e2e
lint:
	cd backend && uv run --extra dev ruff check .
	cd frontend && npm run lint
typecheck:
	cd backend && uv run --extra dev mypy dirima
	cd frontend && npm run typecheck
