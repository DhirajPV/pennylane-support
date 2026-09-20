# Every target is a docker compose command: the only prerequisite is Docker.
COMPOSE ?= docker compose

.PHONY: up down reset migrate seed test logs psql

up:       ## Build and start the stack; API on http://localhost:8000/docs
	$(COMPOSE) up --build

down:     ## Stop the stack, keep the database volume
	$(COMPOSE) down

reset:    ## Stop the stack and delete the database volume
	$(COMPOSE) down -v

migrate:  ## Run migrations against the running stack
	$(COMPOSE) exec app alembic upgrade head

seed:     ## Run the seed against the running stack
	$(COMPOSE) exec app python -m app.seed.load

test:     ## Run pytest in a one-off container, after migrations
	$(COMPOSE) run --rm -e SKIP_SEED=1 app pytest

logs:     ## Follow the app logs
	$(COMPOSE) logs -f app

psql:     ## Open a psql shell on the database
	$(COMPOSE) exec db psql -U pennylane -d pennylane
