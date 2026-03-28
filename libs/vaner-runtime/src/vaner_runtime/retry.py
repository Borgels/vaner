"""Retry middleware with exponential backoff for transient failures.

Distinguishes retryable errors (transient) from terminal errors (permanent).
Integrates with JobStore for retry count tracking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from .job_store import JobStore

T = TypeVar("T")

# Errors that indicate transient failures — worth retrying
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,  # covers network issues
)

# HTTP status codes that are retryable (when raised as exceptions with .status_code)
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

_log = logging.getLogger("vaner.retry")


def is_retryable(exc: BaseException) -> bool:
    """Return True if this exception represents a transient failure."""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    # Check for httpx/requests-style exceptions with status_code
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    if isinstance(status, int) and status in RETRYABLE_HTTP_STATUSES:
        return True
    return False


async def with_retry(
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_max: float = 60.0,
    job_store: JobStore | None = None,
    job_id: str | None = None,
    **kwargs: Any,
) -> T:
    """Execute async fn with exponential backoff retry.

    On each failure, waits backoff_base ** attempt seconds (capped at backoff_max).
    Retryable errors are retried up to max_attempts total.
    Terminal errors raise immediately without retry.
    If job_store and job_id are provided, updates retry_count on each retry.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt == max_attempts - 1:
                raise
            wait = min(backoff_base ** attempt, backoff_max)
            _log.warning(
                "Retryable error on attempt %d/%d, waiting %.1fs: %s",
                attempt + 1,
                max_attempts,
                wait,
                exc,
            )
            if job_store is not None and job_id is not None:
                job_store.increment_retry(job_id)
            await asyncio.sleep(wait)
    raise last_exc  # unreachable but satisfies type checker


def sync_retry(
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_max: float = 60.0,
    job_store: JobStore | None = None,
    job_id: str | None = None,
    **kwargs: Any,
) -> T:
    """Execute synchronous fn with exponential backoff retry.

    On each failure, waits backoff_base ** attempt seconds (capped at backoff_max).
    Retryable errors are retried up to max_attempts total.
    Terminal errors raise immediately without retry.
    If job_store and job_id are provided, updates retry_count on each retry.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt == max_attempts - 1:
                raise
            wait = min(backoff_base ** attempt, backoff_max)
            _log.warning(
                "Retryable error on attempt %d/%d, waiting %.1fs: %s",
                attempt + 1,
                max_attempts,
                wait,
                exc,
            )
            if job_store is not None and job_id is not None:
                job_store.increment_retry(job_id)
            time.sleep(wait)
    raise last_exc  # unreachable but satisfies type checker
