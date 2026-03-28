"""Tests for vaner_runtime.job_store."""

from __future__ import annotations

import threading
import time

import pytest
from vaner_runtime.job_store import JobStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "test_jobs.db")


# ---------------------------------------------------------------------------
# Creation and retrieval
# ---------------------------------------------------------------------------


def test_create_and_get_job(store):
    job_id = store.create_job(
        workflow_type="file_summary",
        idempotency_key="key-1",
        context_key="ctx-1",
    )
    job = store.get_job(job_id)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["status"] == "pending"
    assert job["workflow_type"] == "file_summary"
    assert job["context_key"] == "ctx-1"
    assert job["retry_count"] == 0
    assert job["max_retries"] == 3


def test_get_nonexistent_job(store):
    assert store.get_job("does-not-exist") is None


def test_get_by_idempotency_key(store):
    job_id = store.create_job("wf", "idem-42")
    result = store.get_by_idempotency_key("idem-42")
    assert result is not None
    assert result["job_id"] == job_id


def test_get_by_missing_idempotency_key(store):
    assert store.get_by_idempotency_key("nonexistent") is None


# ---------------------------------------------------------------------------
# Status transitions — legal
# ---------------------------------------------------------------------------


def test_pending_to_running(store):
    job_id = store.create_job("wf", "t1")
    store.update_status(job_id, "running")
    assert store.get_job(job_id)["status"] == "running"


def test_pending_to_cancelled(store):
    job_id = store.create_job("wf", "t2")
    store.update_status(job_id, "cancelled")
    assert store.get_job(job_id)["status"] == "cancelled"


def test_running_to_completed(store):
    job_id = store.create_job("wf", "t3")
    store.update_status(job_id, "running")
    store.update_status(job_id, "completed")
    assert store.get_job(job_id)["status"] == "completed"


def test_running_to_failed(store):
    job_id = store.create_job("wf", "t4")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed", error="oops")
    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["last_error"] == "oops"


def test_failed_to_pending_retry(store):
    job_id = store.create_job("wf", "t5")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.update_status(job_id, "pending")
    assert store.get_job(job_id)["status"] == "pending"


def test_failed_to_dead_letter(store):
    job_id = store.create_job("wf", "t6")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.update_status(job_id, "dead_letter")
    assert store.get_job(job_id)["status"] == "dead_letter"


def test_dead_letter_to_pending(store):
    job_id = store.create_job("wf", "t7")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.update_status(job_id, "dead_letter")
    store.update_status(job_id, "pending")
    assert store.get_job(job_id)["status"] == "pending"


# ---------------------------------------------------------------------------
# Status transitions — illegal
# ---------------------------------------------------------------------------


def test_illegal_transition_pending_to_completed(store):
    job_id = store.create_job("wf", "ill-1")
    with pytest.raises(ValueError, match="Illegal status transition"):
        store.update_status(job_id, "completed")


def test_illegal_transition_completed_is_terminal(store):
    job_id = store.create_job("wf", "ill-2")
    store.update_status(job_id, "running")
    store.update_status(job_id, "completed")
    with pytest.raises(ValueError, match="Illegal status transition"):
        store.update_status(job_id, "pending")


def test_illegal_transition_cancelled_is_terminal(store):
    job_id = store.create_job("wf", "ill-3")
    store.update_status(job_id, "cancelled")
    with pytest.raises(ValueError, match="Illegal status transition"):
        store.update_status(job_id, "running")


def test_update_nonexistent_job_raises(store):
    with pytest.raises(KeyError):
        store.update_status("ghost", "running")


def test_invalid_status_value(store):
    job_id = store.create_job("wf", "inv-1")
    with pytest.raises(ValueError, match="Invalid status"):
        store.update_status(job_id, "exploded")


# ---------------------------------------------------------------------------
# Idempotency key deduplication
# ---------------------------------------------------------------------------


def test_duplicate_idempotency_key_non_terminal_raises(store):
    store.create_job("wf", "dup-1")
    with pytest.raises(ValueError, match="non-terminal state"):
        store.create_job("wf", "dup-1")


def test_duplicate_idempotency_key_after_completed_allowed(store):
    job_id1 = store.create_job("wf", "dup-2")
    store.update_status(job_id1, "running")
    store.update_status(job_id1, "completed")
    # Should succeed since previous is terminal
    job_id2 = store.create_job("wf", "dup-2")
    assert job_id2 != job_id1
    assert store.get_job(job_id2)["status"] == "pending"


def test_duplicate_idempotency_key_after_cancelled_allowed(store):
    job_id1 = store.create_job("wf", "dup-3")
    store.update_status(job_id1, "cancelled")
    job_id2 = store.create_job("wf", "dup-3")
    assert job_id2 != job_id1


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_saves_data(store):
    job_id = store.create_job("wf", "chk-1")
    store.checkpoint(job_id, {"step": "analyze", "progress": 0.5})
    job = store.get_job(job_id)
    assert job["checkpoint_data"] == {"step": "analyze", "progress": 0.5}
    assert job["status"] == "running"


def test_checkpoint_pending_becomes_running(store):
    job_id = store.create_job("wf", "chk-2")
    assert store.get_job(job_id)["status"] == "pending"
    store.checkpoint(job_id, {"node": "start"})
    assert store.get_job(job_id)["status"] == "running"


def test_checkpoint_running_stays_running(store):
    job_id = store.create_job("wf", "chk-3")
    store.update_status(job_id, "running")
    store.checkpoint(job_id, {"node": "step2"})
    assert store.get_job(job_id)["status"] == "running"


def test_checkpoint_overwrites(store):
    job_id = store.create_job("wf", "chk-4")
    store.checkpoint(job_id, {"v": 1})
    store.checkpoint(job_id, {"v": 2})
    assert store.get_job(job_id)["checkpoint_data"]["v"] == 2


def test_checkpoint_nonexistent_raises(store):
    with pytest.raises(KeyError):
        store.checkpoint("ghost", {"x": 1})


# ---------------------------------------------------------------------------
# cancel_by_context_key
# ---------------------------------------------------------------------------


def test_cancel_by_context_key(store):
    j1 = store.create_job("wf", "ctx-j1", context_key="ctx-A")
    j2 = store.create_job("wf", "ctx-j2", context_key="ctx-A")
    j3 = store.create_job("wf", "ctx-j3", context_key="ctx-B")
    store.update_status(j1, "running")

    count = store.cancel_by_context_key("ctx-A")
    assert count == 2
    assert store.get_job(j1)["status"] == "cancelled"
    assert store.get_job(j2)["status"] == "cancelled"
    # Different context_key — unaffected
    assert store.get_job(j3)["status"] == "pending"


def test_cancel_by_context_key_skips_terminal(store):
    j1 = store.create_job("wf", "ctx-t1", context_key="ctx-C")
    store.update_status(j1, "running")
    store.update_status(j1, "completed")

    count = store.cancel_by_context_key("ctx-C")
    assert count == 0
    assert store.get_job(j1)["status"] == "completed"


# ---------------------------------------------------------------------------
# Quarantine and release
# ---------------------------------------------------------------------------


def test_quarantine_sets_dead_letter(store):
    job_id = store.create_job("wf", "q-1")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.quarantine(job_id, ttl_seconds=60.0)
    job = store.get_job(job_id)
    assert job["status"] == "dead_letter"
    assert job["quarantine_until"] > time.time()


def test_quarantine_via_method(store):
    job_id = store.create_job("wf", "q-2")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.quarantine(job_id, ttl_seconds=3600.0)
    quarantined = store.list_quarantined()
    assert any(j["job_id"] == job_id for j in quarantined)


def test_release_quarantine(store):
    job_id = store.create_job("wf", "q-3")
    store.update_status(job_id, "running")
    store.update_status(job_id, "failed")
    store.quarantine(job_id)
    store.release_quarantine(job_id)
    job = store.get_job(job_id)
    assert job["status"] == "pending"
    assert job["quarantine_until"] is None


def test_release_non_dead_letter_raises(store):
    job_id = store.create_job("wf", "q-4")
    with pytest.raises(ValueError):
        store.release_quarantine(job_id)


def test_quarantine_from_non_failed_raises(store):
    job_id = store.create_job("wf", "q-5")
    with pytest.raises(ValueError):
        store.quarantine(job_id)


# ---------------------------------------------------------------------------
# list_resumable
# ---------------------------------------------------------------------------


def test_list_resumable_returns_only_running(store):
    j1 = store.create_job("wf", "res-1")
    j2 = store.create_job("wf", "res-2")
    j3 = store.create_job("wf", "res-3")
    store.update_status(j1, "running")
    store.update_status(j2, "running")
    store.update_status(j3, "cancelled")

    resumable = store.list_resumable()
    ids = {j["job_id"] for j in resumable}
    assert j1 in ids
    assert j2 in ids
    assert j3 not in ids


# ---------------------------------------------------------------------------
# Concurrent writes
# ---------------------------------------------------------------------------


def test_concurrent_creates_no_corruption(store):
    errors = []
    job_ids = []
    lock = threading.Lock()

    def create_job(n):
        try:
            jid = store.create_job("wf", f"concurrent-{n}", context_key="batch")
            with lock:
                job_ids.append(jid)
        except Exception as e:
            with lock:
                errors.append(str(e))

    threads = [threading.Thread(target=create_job, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent creates: {errors}"
    assert len(job_ids) == 10
    # All jobs should be distinct
    assert len(set(job_ids)) == 10
    # All should be retrievable
    for jid in job_ids:
        job = store.get_job(jid)
        assert job is not None
        assert job["status"] == "pending"


# ---------------------------------------------------------------------------
# purge_completed
# ---------------------------------------------------------------------------


def test_purge_completed_removes_old_terminal_jobs(store):
    j1 = store.create_job("wf", "purge-1")
    j2 = store.create_job("wf", "purge-2")
    j3 = store.create_job("wf", "purge-3")

    store.update_status(j1, "running")
    store.update_status(j1, "completed")
    store.update_status(j2, "cancelled")
    store.update_status(j3, "running")

    # Backdate j1 and j2 to be old
    import sqlite3
    conn = sqlite3.connect(str(store._db_path))
    conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id IN (?, ?)", (time.time() - 90000, j1, j2))
    conn.commit()
    conn.close()

    purged = store.purge_completed(older_than_seconds=86400.0)
    assert purged == 2
    assert store.get_job(j1) is None
    assert store.get_job(j2) is None
    # Running job not touched
    assert store.get_job(j3) is not None


def test_purge_completed_keeps_recent_terminal_jobs(store):
    j1 = store.create_job("wf", "purge-k1")
    store.update_status(j1, "running")
    store.update_status(j1, "completed")
    # updated_at is now, so within threshold
    purged = store.purge_completed(older_than_seconds=86400.0)
    assert purged == 0
    assert store.get_job(j1) is not None


# ---------------------------------------------------------------------------
# list_jobs filters
# ---------------------------------------------------------------------------


def test_list_jobs_filter_by_status(store):
    j1 = store.create_job("wf", "list-1")
    j2 = store.create_job("wf", "list-2")
    store.update_status(j1, "running")

    running = store.list_jobs(status="running")
    pending = store.list_jobs(status="pending")
    assert any(j["job_id"] == j1 for j in running)
    assert any(j["job_id"] == j2 for j in pending)


def test_list_jobs_filter_by_context_key(store):
    j1 = store.create_job("wf", "list-c1", context_key="x")
    store.create_job("wf", "list-c2", context_key="y")

    jobs = store.list_jobs(context_key="x")
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == j1


# ---------------------------------------------------------------------------
# increment_retry
# ---------------------------------------------------------------------------


def test_increment_retry(store):
    job_id = store.create_job("wf", "retry-inc-1")
    assert store.get_job(job_id)["retry_count"] == 0
    count = store.increment_retry(job_id)
    assert count == 1
    count = store.increment_retry(job_id)
    assert count == 2
