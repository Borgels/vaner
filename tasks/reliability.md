# Reliability — Crash Recovery + Cancellation
# Issues: #13 (crash recovery), #14 (cancellation/invalidation)
# Run with: python work.py --plan tasks/reliability.md --yes

# ── Task 1: Crash recovery ──────────────────────────────────────────────────
Read apps/vaner-daemon/src/vaner_daemon/daemon.py, apps/vaner-daemon/src/vaner_daemon/preparation_engine/engine.py, and libs/vaner-runtime/src/vaner_runtime/job_store.py carefully before writing anything.

Add crash recovery to the preparation engine: when the daemon starts, check .vaner/preparation.db for any LangGraph checkpoints that were in-progress (not in a terminal state). For each in-progress checkpoint, re-invoke the preparation graph with the same thread_id so it resumes from where it left off.

Implement this in a new method PreparationEngine.recover_in_progress_runs() in apps/vaner-daemon/src/vaner_daemon/preparation_engine/engine.py. Call it from VanerDaemon.start() after self._preparation_engine.start(). The method should: (1) open the SqliteSaver db at .vaner/preparation.db, (2) query for distinct thread_ids that have checkpoints, (3) for each thread_id get the latest checkpoint and check if its 'next' field is not empty (meaning the graph did not finish), (4) re-invoke self._graph.ainvoke with that thread_id to resume. Handle exceptions per thread_id — one bad checkpoint must not block others. Log each recovery attempt with INFO level.

Write tests in apps/vaner-daemon/tests/test_crash_recovery.py:
- test_recovery_resumes_in_progress: create a fake checkpoint in a temp .vaner/preparation.db, call recover_in_progress_runs(), verify the graph was invoked with that thread_id (mock _graph.ainvoke)
- test_recovery_skips_completed: checkpoint with empty 'next' → not re-invoked
- test_recovery_handles_corrupt_checkpoint: malformed checkpoint data → logs error, does not crash

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/test_crash_recovery.py -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/vaner_daemon/preparation_engine/engine.py --ignore E501,D,T201,ANN
Fix all failures before reporting done.

# ── Task 2: Dead-letter queue ───────────────────────────────────────────────
Read libs/vaner-runtime/src/vaner_runtime/job_store.py carefully.

Add a dead-letter mechanism to JobStore. A job that fails more than MAX_RETRIES times (default: 3) should be moved to a 'dead_letter' status instead of 'failed'. Add: (1) a 'retry_count' INTEGER column to the jobs table (default 0, add via ALTER TABLE IF NOT EXISTS pattern so existing DBs are not broken), (2) a method JobStore.increment_retry(job_id) -> int that increments retry_count and returns the new value, (3) a method JobStore.mark_dead_letter(job_id, reason: str) that sets status='dead_letter' and stores reason in a new 'error_message' TEXT column, (4) a method JobStore.list_dead_letter() -> list[JobRecord] that returns all dead-letter jobs, (5) update the retry middleware in libs/vaner-runtime/src/vaner_runtime/retry.py so that on final failure it calls mark_dead_letter instead of mark_failed.

The 'error_message' column: add via ALTER TABLE IF NOT EXISTS pattern (same as retry_count). Keep backwards compat — existing code that creates JobStore must not break.

Write tests in libs/vaner-runtime/tests/test_dead_letter.py:
- test_increment_retry_counts_up
- test_mark_dead_letter_sets_status
- test_list_dead_letter_returns_only_dead
- test_retry_middleware_dead_letters_after_max_retries (mock a failing function, run with_retry, verify dead_letter called after MAX_RETRIES)

Run: apps/vaner-daemon/.venv/bin/pytest libs/vaner-runtime/tests/test_dead_letter.py -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check libs/vaner-runtime/src/ --ignore E501,D,T201,ANN
Fix all failures. Then run the full test suite: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-runtime/tests/ -q

# ── Task 3: Cancellation and invalidation ──────────────────────────────────
Read apps/vaner-daemon/src/vaner_daemon/preparation_engine/engine.py and apps/vaner-daemon/src/vaner_daemon/state_engine.py.

The PreparationEngine._on_context_invalidated already cancels asyncio Tasks. Make it also persist the cancellation so that after a crash-recovery the cancelled jobs are not re-resumed.

Add to PreparationEngine: a method _mark_context_cancelled(context_key: str) that writes a record to a new 'cancelled_contexts' table in .vaner/preparation.db (columns: context_key TEXT PRIMARY KEY, cancelled_at REAL). Add a method _is_context_cancelled(context_key: str) -> bool. In recover_in_progress_runs(), skip any thread_id whose context_key appears in cancelled_contexts. Call _mark_context_cancelled from _on_context_invalidated.

Also add a cleanup: on daemon start, delete cancelled_contexts entries older than 24 hours (they're only needed to suppress recovery).

Write tests in apps/vaner-daemon/tests/test_cancellation.py:
- test_cancellation_persisted: after _on_context_invalidated, _is_context_cancelled returns True
- test_recovery_skips_cancelled_context: cancelled context_key → not re-invoked during recovery
- test_old_cancellations_cleaned_up: entry older than 24h → deleted on startup

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/test_cancellation.py -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/vaner_daemon/preparation_engine/ --ignore E501,D,T201,ANN
Fix all failures. Run full suite: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-runtime/tests/ -q

# ── Task 4: Commit ──────────────────────────────────────────────────────────
Run the full test suite one final time: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-runtime/tests/ -q
If all pass, run: cd /home/abo/repos/Vaner && git add apps/vaner-daemon/ libs/vaner-runtime/ && git commit -m "feat(reliability): crash recovery, dead-letter queue, cancellation persistence

- PreparationEngine.recover_in_progress_runs(): resumes in-progress LangGraph checkpoints on restart
- JobStore: retry_count + error_message columns, increment_retry(), mark_dead_letter(), list_dead_letter()
- Retry middleware: calls mark_dead_letter after MAX_RETRIES exhausted
- Cancellation: persisted to cancelled_contexts table, suppresses recovery for stale context_keys
- Cancelled contexts older than 24h cleaned up on daemon start

Closes #13 (crash recovery), #14 (cancellation/invalidation), #12 (dead-letter queue), #11 (retry/backoff)

Tests: all passing" && git push origin develop
