"""
Inbound message dispatch: long-lived Telethon client per authorized tenant, POST to
callback_url on each NewMessage(incoming=True), with HMAC signing and retries.

**Why MTProto doesn't use Telegram webhooks for user accounts:**
Telegram's Bot API offers webhooks: you set a URL, Telegram POSTs updates to it. That
applies only to *bots*. User (client) accounts use the MTProto API. MTProto is
session-based and connection-oriented: you maintain a long-lived connection, and
updates (including new messages) are pushed over that connection. There is no webhook
concept for user accounts—you must stay connected and handle updates in your client.

**Why we implement our own callback:**
We use Telethon (MTProto) to act as the user's Telegram client. We receive updates
via the live connection. To integrate with tenants' systems (e.g. notification services,
automation), we need to push those events somewhere. So we implement a callback: when
we receive an incoming message, we POST it to the tenant's `callback_url`. That gives
tenants a webhook-like experience (they receive HTTP POSTs for new messages) while we
handle the MTProto connection and update loop ourselves.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import events
from telethon.tl.types import User

from config import CALLBACK_SIGNING_SECRET
from database import SessionLocal
from models.tenant import Tenant
from models.tenant_auth import TenantAuth
from telethon_manager import build_client

logger = logging.getLogger(__name__)

_clients: dict[uuid.UUID, Any] = {}
_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
_lock = asyncio.Lock()

CALLBACK_MAX_ATTEMPTS = 5
CALLBACK_INITIAL_BACKOFF_SEC = 1.0
CALLBACK_BACKOFF_MULTIPLIER = 2.0
CALLBACK_TIMEOUT_SEC = 30.0


def _compute_signature(body: bytes) -> str:
    """HMAC-SHA256 of body with CALLBACK_SIGNING_SECRET; return hex digest."""
    if not (CALLBACK_SIGNING_SECRET or "").strip():
        return ""
    raw = (CALLBACK_SIGNING_SECRET or "").strip().encode("utf-8")
    sig = hmac.new(raw, body, hashlib.sha256).hexdigest()
    return sig


def _build_headers_and_body(payload: dict[str, Any]) -> tuple[dict[str, str], bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = _compute_signature(body)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if signature:
        headers["X-Signature"] = f"sha256={signature}"
    return headers, body


async def _post_callback(
    url: str,
    payload: dict[str, Any],
    tenant_id: uuid.UUID,
) -> bool:
    """
    POST payload to url with X-Signature header. Retry on 5xx with exponential backoff;
    drop after CALLBACK_MAX_ATTEMPTS. Log failures. Returns True if any attempt succeeded.
    """
    headers, body = _build_headers_and_body(payload)

    last_exc: Exception | None = None
    backoff = CALLBACK_INITIAL_BACKOFF_SEC

    for attempt in range(1, CALLBACK_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SEC) as client:
                r = await client.post(url, content=body, headers=headers)
            if 200 <= r.status_code < 300:
                return True
            if r.status_code < 500:
                logger.warning(
                    "callback non-retryable failure tenant_id=%s url=%s status=%s",
                    tenant_id,
                    url,
                    r.status_code,
                )
                return False
            last_exc = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last_exc = e
            logger.warning(
                "callback attempt %s/%s tenant_id=%s url=%s error=%s",
                attempt,
                CALLBACK_MAX_ATTEMPTS,
                tenant_id,
                url,
                e,
            )

        if attempt < CALLBACK_MAX_ATTEMPTS:
            await asyncio.sleep(backoff)
            backoff *= CALLBACK_BACKOFF_MULTIPLIER

    logger.error(
        "callback dropped after %s attempts tenant_id=%s url=%s last_error=%s",
        CALLBACK_MAX_ATTEMPTS,
        tenant_id,
        url,
        last_exc,
    )
    return False


async def send_test_callback(tenant_id: uuid.UUID, callback_url: str) -> tuple[bool, str]:
    """
    POST a test callback payload to callback_url. Single attempt, no retries.
    Returns (success, error_message).
    """
    from datetime import datetime, timezone

    payload: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "event": "message",
        "message": {
            "chat_id": 0,
            "message_id": 0,
            "sender_id": 0,
            "sender_username": "test",
            "text": "Test callback from Grey TG admin.",
            "date": datetime.now(timezone.utc).isoformat(),
        },
    }
    headers, body = _build_headers_and_body(payload)
    try:
        async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SEC) as c:
            r = await c.post(callback_url, content=body, headers=headers)
        if 200 <= r.status_code < 300:
            return True, ""
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _payload_from_event(tenant_id: uuid.UUID, event: events.NewMessage.Event) -> dict[str, Any]:
    msg = event.message
    sender = event.sender
    sender_id: int | None = getattr(sender, "id", None) if sender else event.sender_id
    sender_username: str | None = None
    if isinstance(sender, User) and getattr(sender, "username", None):
        sender_username = str(sender.username)
    date_val = msg.date
    date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
    text = (msg.text or "").strip() if msg.text else ""
    message_id: int = getattr(msg, "id", 0) or 0

    return {
        "tenant_id": str(tenant_id),
        "event": "message",
        "message": {
            "chat_id": event.chat_id,
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "text": text,
            "date": date_str,
        },
    }


async def _run_dispatcher(tenant_id: uuid.UUID, callback_url: str) -> None:
    client = build_client(tenant_id)
    async with _lock:
        _clients[tenant_id] = client

    async def on_new_message(event: events.NewMessage.Event) -> None:
        payload = _payload_from_event(tenant_id, event)
        asyncio.create_task(_post_callback(callback_url, payload, tenant_id))

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("dispatcher tenant_id=%s not authorized, skipping", tenant_id)
            return
        client.add_event_handler(on_new_message, events.NewMessage(incoming=True))
        await client.run_until_disconnected()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception("dispatcher tenant_id=%s error=%s", tenant_id, e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        async with _lock:
            _clients.pop(tenant_id, None)
            _tasks.pop(tenant_id, None)


def _get_authorized_tenants_with_callback() -> list[tuple[uuid.UUID, str]]:
    with SessionLocal() as db:
        stmt = (
            select(Tenant.id, Tenant.callback_url)
            .join(TenantAuth, TenantAuth.tenant_id == Tenant.id)
            .where(TenantAuth.authorized.is_(True), Tenant.callback_url.is_not(None))
        )
        rows = db.execute(stmt).all()
    return [(r[0], r[1]) for r in rows if r[1]]


async def start_dispatcher(tenant_id: uuid.UUID, callback_url: str) -> None:
    """Start inbound dispatcher for tenant if not already running."""
    async with _lock:
        if tenant_id in _tasks:
            return
        task = asyncio.create_task(_run_dispatcher(tenant_id, callback_url))
        _tasks[tenant_id] = task


async def stop_dispatcher(tenant_id: uuid.UUID) -> None:
    """Stop dispatcher for tenant: disconnect client and cancel task."""
    async with _lock:
        task = _tasks.get(tenant_id)
        client = _clients.get(tenant_id)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    async with _lock:
        _clients.pop(tenant_id, None)
        _tasks.pop(tenant_id, None)


async def start_all_dispatchers() -> None:
    """Start dispatchers for all authorized tenants with callback_url (e.g. on app startup)."""
    for tenant_id, callback_url in _get_authorized_tenants_with_callback():
        await start_dispatcher(tenant_id, callback_url)


async def stop_all_dispatchers() -> None:
    """Stop all running dispatchers (e.g. on app shutdown)."""
    async with _lock:
        ids = list(_tasks.keys())
    for tenant_id in ids:
        await stop_dispatcher(tenant_id)
