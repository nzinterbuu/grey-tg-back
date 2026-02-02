"""
Dev-only in-memory callback receiver for local testing.

When DEV_CALLBACK_RECEIVER=1, POST /dev/callback-receiver stores incoming JSON payloads
in memory and GET returns them. Use a tenant's callback_url =
http://localhost:8000/dev/callback-receiver to capture inbound dispatches during development.

Not mounted when DEV_CALLBACK_RECEIVER is unset (production).
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

MAX_PAYLOADS = 100
_store: deque[dict[str, Any]] = deque(maxlen=MAX_PAYLOADS)

router = APIRouter(prefix="/dev/callback-receiver", tags=["dev"])


@router.post("")
async def post_callback(request: Request) -> dict[str, str]:
    """Store request body (JSON) in memory. Returns 200."""
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"_raw": raw.decode("utf-8", errors="replace")}
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    _store.append(entry)
    return {"ok": "stored"}


@router.get("")
def get_callback_payloads() -> list[dict[str, Any]]:
    """Return recent stored payloads (newest first)."""
    return list(reversed(_store))
