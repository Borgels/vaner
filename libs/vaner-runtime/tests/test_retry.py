"""Tests for vaner_runtime.retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from vaner_runtime.retry import is_retryable, sync_retry, with_retry

# ---------------------------------------------------------------------------
# is_retryable
# ---------------------------------------------------------------------------


def test_connection_error_is_retryable():
    assert is_retryable(ConnectionError("conn refused"))


def test_timeout_error_is_retryable():
    assert is_retryable(TimeoutError("timed out"))


def test_os_error_is_retryable():
    assert is_retryable(OSError("network unreachable"))


def test_value_error_not_retryable():
    assert not is_retryable(ValueError("bad input"))


def test_runtime_error_not_retryable():
    assert not is_retryable(RuntimeError("something broke"))


def test_http_429_is_retryable():
    exc = Exception("rate limited")
    exc.status_code = 429
    assert is_retryable(exc)


def test_http_503_is_retryable():
    exc = Exception("service unavailable")
    exc.status_code = 503
    assert is_retryable(exc)


def test_http_404_not_retryable():
    exc = Exception("not found")
    exc.status_code = 404
    assert not is_retryable(exc)


def test_response_status_code_retryable():
    response = MagicMock()
    response.status_code = 502
    exc = Exception("bad gateway")
    exc.response = response
    assert is_retryable(exc)


# ---------------------------------------------------------------------------
# with_retry (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_on_first_attempt():
    async def fn():
        return 42

    result = await with_retry(fn)
    assert result == 42


@pytest.mark.asyncio
async def test_retryable_error_retries():
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("transient")
        return "ok"

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(fn, max_attempts=3, backoff_base=2.0)

    assert result == "ok"
    assert call_count == 3
    assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_terminal_error_raises_immediately():
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        raise ValueError("bad input")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="bad input"):
            await with_retry(fn, max_attempts=3)

    assert call_count == 1


@pytest.mark.asyncio
async def test_max_attempts_reached_raises_last_exception():
    async def fn():
        raise ConnectionError("always fails")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ConnectionError, match="always fails"):
            await with_retry(fn, max_attempts=3)


@pytest.mark.asyncio
async def test_backoff_timing_approximately_correct():
    call_count = 0
    sleep_calls = []

    async def fn():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("fail")

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(ConnectionError):
            await with_retry(fn, max_attempts=3, backoff_base=2.0, backoff_max=60.0)

    # attempt 0 → wait 2^0=1.0, attempt 1 → wait 2^1=2.0
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == pytest.approx(1.0)
    assert sleep_calls[1] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_backoff_capped_at_max():
    sleep_calls = []

    async def fn():
        raise ConnectionError("fail")

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(ConnectionError):
            await with_retry(fn, max_attempts=5, backoff_base=100.0, backoff_max=10.0)

    assert all(s <= 10.0 for s in sleep_calls)


@pytest.mark.asyncio
async def test_job_store_increment_retry_called():
    job_store = MagicMock()
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("transient")
        return "done"

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await with_retry(fn, max_attempts=3, job_store=job_store, job_id="job-123")

    assert job_store.increment_retry.call_count == 2
    job_store.increment_retry.assert_called_with("job-123")


@pytest.mark.asyncio
async def test_job_store_not_called_when_not_provided():
    async def fn():
        raise ConnectionError("fail")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ConnectionError):
            await with_retry(fn, max_attempts=2)
    # No assertion needed — just verifies no AttributeError is raised


# ---------------------------------------------------------------------------
# sync_retry
# ---------------------------------------------------------------------------


def test_sync_retry_success():
    def fn():
        return 99

    assert sync_retry(fn) == 99


def test_sync_retry_retries_on_retryable():
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("transient")
        return "done"

    with patch("time.sleep") as mock_sleep:
        result = sync_retry(fn, max_attempts=3, backoff_base=1.0)

    assert result == "done"
    assert call_count == 3
    assert mock_sleep.call_count == 2


def test_sync_retry_terminal_raises_immediately():
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        raise TypeError("terminal")

    with patch("time.sleep"):
        with pytest.raises(TypeError, match="terminal"):
            sync_retry(fn, max_attempts=3)

    assert call_count == 1


def test_sync_retry_max_attempts_exhausted():
    def fn():
        raise OSError("always fails")

    with patch("time.sleep"):
        with pytest.raises(OSError, match="always fails"):
            sync_retry(fn, max_attempts=3)


def test_sync_retry_increment_called():
    job_store = MagicMock()
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("fail")
        return "ok"

    with patch("time.sleep"):
        sync_retry(fn, max_attempts=3, job_store=job_store, job_id="j-sync")

    job_store.increment_retry.assert_called_once_with("j-sync")
