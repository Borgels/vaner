# Artifact Store — SQLite migration
# Issue: #22
# Run with: python work.py --plan tasks/artifact_sqlite.md --yes

# ── Task 1: SQLite artefact store ───────────────────────────────────────────
Read libs/vaner-tools/src/vaner_tools/artefact_store.py in full before writing anything. Understand all existing functions and the Artefact dataclass (fields: key, kind, source_path, source_mtime, generated_at, model, content).

Replace the file-based JSON storage with SQLite while keeping the EXACT same public API (same function signatures, same return types). The new implementation must:

1. Use a single SQLite database at cache_root/artefacts.db
2. Schema: CREATE TABLE IF NOT EXISTS artefacts (key TEXT PRIMARY KEY, kind TEXT NOT NULL, source_path TEXT NOT NULL, source_mtime REAL NOT NULL, generated_at REAL NOT NULL, model TEXT NOT NULL, content TEXT NOT NULL)
3. Keep all existing functions: write_artefact(), read_artefact(), is_stale(), list_artefacts(), read_repo_index()
4. write_artefact() signature unchanged — it computes key from source_path+kind as before, INSERTs OR REPLACEs into SQLite
5. list_artefacts(cache_root, kind=None) — SELECT from SQLite, filter by kind if provided, return list[Artefact]
6. read_artefact(cache_root, key) — SELECT by key, return Artefact | None
7. is_stale(artefact, max_age_seconds=1800) — unchanged logic, no DB access needed
8. read_repo_index(cache_root) — reads from artefacts.db WHERE kind='file_summary', returns dict[str, str] {source_path: content}
9. Add a migrate_from_json(cache_root) function that reads all existing .json files from the old cache structure and imports them into SQLite, then removes the JSON files. This allows zero-downtime migration.
10. Thread-safe: use a module-level sqlite3.connect with check_same_thread=False and a threading.Lock() around writes.

Do NOT break any existing callers. The Artefact dataclass stays identical. The cache_root parameter stays. All existing tests in libs/vaner-tools/tests/ must still pass.

Write new tests in libs/vaner-tools/tests/test_artefact_sqlite.py:
- test_write_and_read_roundtrip: write_artefact then read_artefact returns same data
- test_list_artefacts_filters_by_kind
- test_write_overwrites_existing_key
- test_is_stale_fresh: generated_at=now, max_age=1800 → False
- test_is_stale_old: generated_at=now-3600 → True
- test_read_repo_index_returns_file_summaries_only
- test_migrate_from_json: create old-style JSON files in temp dir, call migrate_from_json, verify all in SQLite
- test_thread_safety: 10 threads writing concurrently, no corruption

Run: apps/vaner-daemon/.venv/bin/pytest libs/vaner-tools/tests/ -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check libs/vaner-tools/src/ --ignore E501,D,T201,ANN
Fix all failures before proceeding.

# ── Task 2: Update repo-analyzer to use new store ───────────────────────────
Read apps/repo-analyzer/src/analyzer/graph.py and any other files that call write_artefact or list_artefacts from vaner_tools.

Verify that the analyzer still works with the new SQLite store — it uses the same public API so it should require zero changes. Run the analyzer import check: apps/repo-analyzer/.venv/bin/python -c "from analyzer.graph import build_graph; print('OK')"

If there are any import errors or API mismatches, fix them. If everything works, write a short note in the response confirming compatibility.

Run: apps/vaner-daemon/.venv/bin/pytest libs/vaner-tools/tests/ apps/vaner-daemon/tests/ -q
All must pass.

# ── Task 3: Add vaner migrate command ───────────────────────────────────────
Read vaner.py in full. Understand the existing subcommand pattern (init, daemon, analyze, ask).

Add a new subcommand: python vaner.py migrate
This command should: (1) print "Migrating artifact cache from JSON to SQLite...", (2) call migrate_from_json(REPO_ROOT / ".vaner" / "cache"), (3) print the count of artifacts migrated, (4) print "Done. Run 'python vaner.py daemon start' to resume." 

Also update the 'vaner.py daemon status' output to show "artifacts_in_cache: N" by querying the SQLite store count.

Run: apps/vaner-daemon/.venv/bin/python vaner.py migrate (should work on the existing .vaner/cache/)
Verify the count printed is > 0 and matches what was in the JSON cache.

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-tools/tests/ -q

# ── Task 4: Commit ──────────────────────────────────────────────────────────
Run full suite: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-runtime/tests/ libs/vaner-tools/tests/ -q
If all pass: cd /home/abo/repos/Vaner && git add libs/vaner-tools/ apps/vaner-daemon/ vaner.py && git commit -m "feat(storage): migrate artifact store from JSON files to SQLite

- artefact_store.py: SQLite backend (artefacts.db), same public API
- migrate_from_json(): zero-downtime migration from old JSON structure
- Thread-safe writes via module-level Lock
- read_repo_index() now queries SQLite directly (no JSON files needed)
- vaner.py: added 'migrate' subcommand, daemon status shows artifact count

Closes #22 (SQLite artifact store)

Tests: all passing including 8 new SQLite-specific tests" && git push origin develop
