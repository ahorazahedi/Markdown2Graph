.PHONY: help neo4j-up neo4j-down neo4j-logs venv install backend frontend test clean

help:
	@echo "make neo4j-up     - start Neo4j docker container"
	@echo "make neo4j-down   - stop Neo4j docker container"
	@echo "make neo4j-logs   - tail Neo4j logs"
	@echo "make install      - install backend python deps"
	@echo "make backend      - run flask backend (port 8000)"
	@echo "make frontend     - run vite frontend (port 5173)"
	@echo "make test         - run backend pytest suite"
	@echo "make discover P=<folder>   - cli: discover schema from a folder"
	@echo "make ingest P=<folder>     - cli: ingest a folder"
	@echo "make stats        - cli: show graph counts"

neo4j-up:
	./scripts/neo4j_start.sh

neo4j-down:
	./scripts/neo4j_stop.sh

neo4j-logs:
	./scripts/neo4j_logs.sh

venv:
	python3 -m venv backend/.venv

install:
	cd backend && pip install -r requirements.txt

backend:
	cd backend && python -m app.wsgi

frontend:
	cd frontend && npm install --silent && npm run dev

test:
	cd backend && pytest

discover:
	cd backend && python -m app.cli discover $(P)

ingest:
	cd backend && python -m app.cli ingest $(P) $(ARGS)

stats:
	cd backend && python -m app.cli stats

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
