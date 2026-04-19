.PHONY: hygiene lint format-check typecheck test verify hygiene-clean-preview hygiene-clean

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

hygiene-clean-preview:
	git clean -ndX

hygiene-clean:
	git clean -fdX
