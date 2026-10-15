# Prometheus Backend — Developer Commands
# Compatible: Windows (PowerShell/CMD via make) + Linux/macOS

.PHONY: help up down db-migrate db-rollback db-reset dev install setup

help:
	@echo "Prometheus Backend Commands:"
	@echo ""
	@echo "  make up          - Start PostgreSQL + Redis (Docker)"
	@echo "  make down        - Stop containers"
	@echo "  make dev         - Start FastAPI dev server"
	@echo "  make install     - Install Python dependencies"
	@echo "  make db-migrate  - Run Alembic migrations"
	@echo "  make db-rollback - Rollback last migration"
	@echo "  make db-reset    - Drop + recreate DB (DESTRUCTIVE!)"
	@echo "  make db-shell    - Open psql shell"
	@echo "  make setup       - First-time setup: up + migrate"

# ── Docker ────────────────────────────────────────────────────────────────────

up:
	docker compose up -d
	@echo "Waiting for PostgreSQL to be ready..."
	@python scripts/wait_for_db.py
	@echo "PostgreSQL ready!"

down:
	docker compose down

# ── Python ────────────────────────────────────────────────────────────────────

install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Database ──────────────────────────────────────────────────────────────────

db-migrate:
	alembic upgrade head

db-rollback:
	alembic downgrade -1

db-shell:
	docker compose exec postgres psql -U prometheus -d prometheus

db-reset:
	docker compose exec postgres psql -U prometheus -c "DROP DATABASE IF EXISTS prometheus;"
	docker compose exec postgres psql -U prometheus -c "CREATE DATABASE prometheus;"
	alembic upgrade head
	@echo "Database reset complete"

# ── First-time setup ──────────────────────────────────────────────────────────

setup: up db-migrate
	@echo ""
	@echo "Prometheus DB ready!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. make dev"
	@echo "  2. Bootstrap API key:"
	@echo "     curl -X POST http://localhost:8000/api/v1/auth/setup"
	@echo "       -H 'Content-Type: application/json'"
	@echo "       -d '{\"key_name\": \"laptop\"}'"