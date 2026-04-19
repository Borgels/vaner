.PHONY: hygiene lint format-check typecheck test verify lockfiles hygiene-clean-preview hygiene-clean

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

lockfiles:
	docker run --rm -v "$(CURDIR):/work" -w /work python:3.11-slim bash -lc "python -m pip install --quiet pip-tools && pip-compile --generate-hashes --resolver=backtracking requirements/ci.in -o requirements/ci.txt && pip-compile --generate-hashes --resolver=backtracking requirements/fuzz.in -o requirements/fuzz.txt && pip-compile --generate-hashes --resolver=backtracking requirements/release.in -o requirements/release.txt && pip-compile --generate-hashes --resolver=backtracking requirements/runtime.in -o requirements/runtime.txt"

hygiene-clean-preview:
	git clean -ndX

hygiene-clean:
	git clean -fdX
