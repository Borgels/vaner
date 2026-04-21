.PHONY: hygiene lint format-check typecheck test verify lockfiles hygiene-clean-preview hygiene-clean ui-install ui-build ui-dev ui-test build dev dev-mcp dev-proxy

hygiene:
	python devtools/repo_hygiene.py --strict

lint:
	ruff check .

format-check:
	ruff format --check .

typecheck:
	mypy src

test:
	pytest

ui-install:
	cd ui/cockpit && npm ci

ui-build:
	cd ui/cockpit && npm run build

ui-dev:
	cd ui/cockpit && npm run dev

ui-test:
	cd ui/cockpit && npm run test

build: ui-build

# Dev loop: one process rebuilds the SPA into src/vaner/daemon/cockpit_assets/dist
# while the daemon HTTP server serves the built assets at http://127.0.0.1:8473.
# Restart either side after config changes; the cockpit bundle SHA banner
# tells the browser when the running backend is behind the SPA bundle.
dev:
	@echo "Starting Vaner daemon cockpit at http://127.0.0.1:8473 (Ctrl+C to stop)"
	@cd ui/cockpit && npm run build --silent
	@vaner daemon serve-http --host 127.0.0.1 --port 8473

# MCP mode: run MCP stdio plus a cockpit sidecar reachable from a browser.
dev-mcp:
	@echo "Starting Vaner MCP (stdio) with cockpit sidecar at http://127.0.0.1:8473"
	@cd ui/cockpit && npm run build --silent
	@vaner mcp --transport stdio --cockpit --cockpit-host 127.0.0.1 --cockpit-port 8473

# Proxy mode: rebuild the SPA and run the proxy so the cockpit is served
# alongside the /v1/chat/completions route.
dev-proxy:
	@echo "Starting Vaner proxy cockpit at http://127.0.0.1:8472"
	@cd ui/cockpit && npm run build --silent
	@vaner proxy --host 127.0.0.1 --port 8472

verify: hygiene lint format-check typecheck test

lockfiles:
	docker run --rm -v "$(CURDIR):/work" -w /work python:3.11-slim bash -lc "python -m pip install --quiet pip-tools && pip-compile --generate-hashes --resolver=backtracking requirements/ci.in -o requirements/ci.txt && pip-compile --generate-hashes --resolver=backtracking requirements/fuzz.in -o requirements/fuzz.txt && pip-compile --generate-hashes --resolver=backtracking requirements/release.in -o requirements/release.txt && pip-compile --generate-hashes --resolver=backtracking requirements/runtime.in -o requirements/runtime.txt"

hygiene-clean-preview:
	git clean -ndX

hygiene-clean:
	git clean -fdX
