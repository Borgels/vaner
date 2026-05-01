.PHONY: hygiene lint format-check typecheck test verify release-preflight lockfiles hygiene-clean-preview hygiene-clean

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

verify: hygiene lint format-check typecheck test

release-preflight:
	@if [ -z "$(VERSION)" ]; then echo "VERSION is required, e.g. make release-preflight VERSION=0.8.9"; exit 2; fi
	python scripts/release/preflight.py version "$(VERSION)"
	python scripts/release/preflight.py required-checks
	python scripts/release/preflight.py scan-public-assets
	ruff check scripts/release tests/test_release_preflight.py
	ruff format --check scripts/release tests/test_release_preflight.py
	pytest tests/test_release_preflight.py tests/test_internal_boundary_guard.py -q
	python -m pip install --require-hashes -r requirements/release.txt
	rm -rf dist build *.egg-info sbom.json sbom.json.sigstore.json
	SOURCE_DATE_EPOCH="$$(git log -1 --pretty=%ct)" python -m build
	cyclonedx-py requirements requirements/release.txt --output-format json --output-file sbom.json
	for f in dist/* sbom.json; do printf '{"preflight":true,"subject":"%s"}\n' "$$f" > "$$f.sigstore.json"; done
	python scripts/release/preflight.py assets > /tmp/vaner-release-assets.txt
	bash -lc 'mapfile -t assets < /tmp/vaner-release-assets.txt; python scripts/release/preflight.py primary-artifacts "$${assets[@]}" > /tmp/vaner-primary-artifacts.txt'
	bash -lc 'mapfile -t assets < /tmp/vaner-release-assets.txt; python scripts/release/preflight.py slsa-subjects "$${assets[@]}" > /tmp/vaner-slsa-subjects.b64'
	test -s /tmp/vaner-release-assets.txt
	test -s /tmp/vaner-primary-artifacts.txt
	test -s /tmp/vaner-slsa-subjects.b64
	if grep -E '\.sigstore\.json$$' /tmp/vaner-primary-artifacts.txt; then exit 1; fi
	@echo "Local release preflight passed for $(VERSION). Run GitHub Release Preflight on this commit before tagging."

lockfiles:
	docker run --rm -v "$(CURDIR):/work" -w /work python:3.11-slim bash -lc "python -m pip install --quiet pip-tools && pip-compile --generate-hashes --resolver=backtracking requirements/ci.in -o requirements/ci.txt && pip-compile --generate-hashes --resolver=backtracking requirements/fuzz.in -o requirements/fuzz.txt && pip-compile --generate-hashes --resolver=backtracking requirements/release.in -o requirements/release.txt && pip-compile --generate-hashes --resolver=backtracking requirements/runtime.in -o requirements/runtime.txt"

hygiene-clean-preview:
	git clean -ndX

hygiene-clean:
	git clean -fdX
