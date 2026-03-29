# Issue #1: Replace MemorySaver with SqliteSaver for persistent conversation memory
# Status: COMPLETE — implemented via AsyncSqliteSaver in broker and supervisor graphs

# ── Summary ───────────────────────────────────────────────────────────────────
# Both graph.py files now use AsyncSqliteSaver (async-safe SQLite checkpointer):
#   - apps/vaner-builder/src/agent/graph.py  → .vaner/memory.db
#   - apps/supervisor/src/supervisor/graph.py → .vaner/supervisor.db
#
# The build_graph() factory in each module opens an aiosqlite connection and
# wraps it with AsyncSqliteSaver, which is passed to _builder.compile().
#
# The .vaner/ directory is created with os.makedirs(..., exist_ok=True) before
# the DB path is defined, so the first run automatically creates the database.

# ── Acceptance criteria (all met) ─────────────────────────────────────────────
# [x] Running vaner.py twice with --thread dev preserves conversation context
# [x] .vaner/memory.db and .vaner/supervisor.db are created on first run
# [x] Broker (vaner-builder) and supervisor both use SqliteSaver (AsyncSqliteSaver)

# ── Implementation notes ──────────────────────────────────────────────────────
# We use langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver rather than the
# synchronous SqliteSaver because all graph nodes are async. The pattern is:
#
#   _conn_cm = aiosqlite.connect(DB_PATH)
#   conn = await _conn_cm.__aenter__()
#   checkpointer = AsyncSqliteSaver(conn)
#   return _builder.compile(..., checkpointer=checkpointer)
#
# A top-level `graph` (no checkpointer) is also exported for import-time
# validation and unit tests.
