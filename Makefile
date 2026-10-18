.PHONY: setup test lint run lab-dev lab-roundtrip scene-pins e2e soak docker-smoke

setup:
	uv sync --frozen --all-extras

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

run:
	uv run uvicorn conductor.app:app --reload --port 8000

# The Design Lab on the simulator: the Studio's Lab panel at http://127.0.0.1:8200/ops/showcase/#lab, auth off
# (this machine only), a throwaway data dir, every HUBSPACE_* and secret variable stripped. Never port 8000: that is
# the show Conductor's (scripts/dev_design_lab.py refuses it).
lab-dev:
	uv run --offline python scripts/dev_design_lab.py

# Slow (minutes per edit): export the canonical Design Lab edits, apply each to a scratch copy of the working tree and
# run the whole suite there (tests/test_lab_roundtrip.py, skipped unless LAB_ROUNDTRIP=1).
lab-roundtrip:
	LAB_ROUNDTRIP=1 uv run --offline pytest -q tests/test_lab_roundtrip.py -p no:cacheprovider

# After a hand edit of config/{scenes,modes,recipes}.yaml: regenerate tests/fixtures_scene_frames.json (frames + the
# approved-value _pins), then review its diff.
scene-pins:
	uv run --offline python scripts/regen_scene_pins.py

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
