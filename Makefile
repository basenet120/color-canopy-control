.PHONY: setup test lint run guest-dev e2e soak docker-smoke

setup:
	uv sync --frozen --all-extras

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

run:
	uv run uvicorn conductor.app:app --reload --port 8000

# The guest panel on the simulator, no hardware: both shop modules mapped, CONDUCTOR_ENV=workshop-sim, auth off, a
# throwaway data dir. Open http://127.0.0.1:8100/kiosk/?debug=1 (scripts/dev_guest_panel.py).
guest-dev:
	uv run python scripts/dev_guest_panel.py

# Playwright end-to-end tests and screenshots, on the simulator. Not part of `make test` (CI has no browser).
e2e:
	uv run pytest -q tests_e2e

# 8 simulated hours against config/sim/acceptance.yaml on the virtual clock; writes docs/reports/sim-acceptance.md.
soak:
	uv run python -m conductor.soak

# The same container smoke test CI runs.
docker-smoke:
	docker build -t conductor:local .
	docker rm -f conductor-smoke >/dev/null 2>&1 || true
	docker run -d --name conductor-smoke -p 8765:8000 -e CONDUCTOR_BASIC_AUTH=ci:ci conductor:local
	for i in $$(seq 1 30); do curl -fsS localhost:8765/healthz && break; sleep 1; done
	test "$$(curl -s -o /dev/null -w '%{http_code}' localhost:8765/api/canopy/layout)" = 401
	test "$$(curl -s -o /dev/null -w '%{http_code}' -u ci:ci localhost:8765/api/canopy/layout)" = 200
	docker exec conductor-smoke python -c "import aioafero"
	docker logs conductor-smoke 2>&1 | tail -20
	docker rm -f conductor-smoke
