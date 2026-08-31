VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: dev install test doctor build clean

dev: ## start backend + frontend + browser
	./run.sh

install: $(VENV)
	$(PIP) install --quiet -e ".[dev]"
	cd frontend && npm install

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

test:
	$(VENV)/bin/pytest -q

doctor:
	$(VENV)/bin/aicvtailor doctor

build:
	cd frontend && npm run build

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
