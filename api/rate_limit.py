"""
Per-tenant in-memory rate limiter for send-message and similar actions.

**Why rate limiting:**
- Telegram enforces flood limits; excessive requests trigger FloodWaitError and block the
  client. Rate limiting reduces the chance of hitting those limits.
- Prevents a single tenant from exhausting API capacity and degrading service for others.
- Protects against buggy retry loops or runaway clients.
- Per-tenant isolation ensures one tenant cannot starve others.

**MVP:** In-memory, sliding-window. Not shared across processes; suitable for single-instance
dev/small deployments. For production at scale, use Redis or similar for cross-worker limits.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from uuid import UUID

_lock = Lock()
_store: dict[UUID, list[float]] = defaultdict(list)

# 10 requests per 60 seconds per tenant
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SEC = 60.0


def check_rate_limit(tenant_id: UUID) -> tuple[bool, float | None]:
    """
    Returns (allowed, retry_after_seconds).
    If allowed is False, retry_after_seconds is the suggested wait (or None if unknown).
    """
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SEC
    with _lock:
        times = _store[tenant_id]
        times[:] = [t for t in times if t > cutoff]
        if len(times) >= RATE_LIMIT_REQUESTS:
            oldest = min(times)
            retry_after = max(0.0, RATE_LIMIT_WINDOW_SEC - (now - oldest))
            return False, round(retry_after, 1)
        times.append(now)
    return True, None
