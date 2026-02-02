"""
Telethon session management per tenant.

Why sessions must be treated like passwords:
  - A Telethon session string grants full access to the Telegram account (read messages,
    send as the user, join chats, etc.). Anyone with the string can impersonate the user.
  - Session strings are long-lived and remain valid until explicitly revoked.
  - They must be stored encrypted at rest (e.g. Fernet) and never logged or exposed.

Why DB storage is needed for multi-tenancy:
  - Each tenant has its own Telegram account(s). We need to persist which session
    belongs to which tenant and load it on demand.
  - File-based sessions (e.g. .session SQLite) don't scale: we'd need a separate file
    per tenant, path management, and backup complexity. A DB centralizes storage and
    works with connection pooling, replication, and app servers that don't share disk.
  - Encrypted session strings in a DB allow multiple workers to serve any tenant
    without shared filesystem, and we can back up or migrate tenant data consistently.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import TELEGRAM_API_HASH, TELEGRAM_API_ID
from database import SessionLocal
from models.tenant_auth import TenantAuth
from session_crypto import decrypt_session, encrypt_session


def _get_or_create_auth(tenant_id: uuid.UUID, db: Session) -> TenantAuth:
    stmt = select(TenantAuth).where(TenantAuth.tenant_id == tenant_id)
    row = db.execute(stmt).scalars().first()
    if row:
        return row
    auth = TenantAuth(tenant_id=tenant_id)
    db.add(auth)
    db.commit()
    db.refresh(auth)
    return auth


def build_client(
    tenant_id: uuid.UUID,
    db: Session | None = None,
) -> TelegramClient:
    """
    Return a TelegramClient for the given tenant using a StringSession.

    Loads the tenant's encrypted session from DB, decrypts it, and builds a client.
    If no session exists yet, uses an empty StringSession (for first-time login).
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in env.")

    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        auth = _get_or_create_auth(tenant_id, db)
        raw = ""
        if auth.session_string:
            raw = decrypt_session(auth.session_string)
        session = StringSession(raw)
        client = TelegramClient(
            session,
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        )
        return client
    finally:
        if own_db:
            db.close()


async def save_session(
    tenant_id: uuid.UUID,
    client: TelegramClient,
    db: Session | None = None,
    authorized: bool = True,
) -> None:
    """
    Persist the client's session string for the tenant (encrypt before storing).

    Args:
        tenant_id: Tenant UUID
        client: TelegramClient instance
        db: Database session (optional)
        authorized: Whether the session is fully authorized (default True).
                    Set False when saving session after send_code_request().
    
    Updates TenantAuth with encrypted session_string. If authorized=True,
    also sets authorized=True and phone (if available).
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        auth = _get_or_create_auth(tenant_id, db)
        raw = client.session.save()
        encrypted = encrypt_session(raw)
        auth.session_string = encrypted
        auth.last_error = None
        auth.updated_at = datetime.now(timezone.utc)
        
        if authorized:
            auth.authorized = True
            try:
                me = await client.get_me()
                if me and me.phone:
                    auth.phone = me.phone
            except Exception:
                pass
        # If not authorized, keep existing authorized/phone values

        db.add(auth)
        db.commit()
    finally:
        if own_db:
            db.close()


def set_last_error(
    tenant_id: uuid.UUID,
    message: str,
    db: Session | None = None,
) -> None:
    """Store last auth error for the tenant (returned by GET /tenants/{id}/status)."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        auth = _get_or_create_auth(tenant_id, db)
        auth.last_error = message
        auth.updated_at = datetime.now(timezone.utc)
        db.add(auth)
        db.commit()
    finally:
        if own_db:
            db.close()


def clear_session(
    tenant_id: uuid.UUID,
    db: Session | None = None,
) -> None:
    """
    Clear stored session for the tenant (session_string, authorized, phone, last_error).
    Call after log_out(); does not disconnect or log out the client itself.
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        auth = _get_or_create_auth(tenant_id, db)
        auth.session_string = None
        auth.authorized = False
        auth.phone = None
        auth.phone_code_hash = None
        auth.code_requested_at = None
        auth.code_timeout_seconds = None
        auth.last_error = None
        auth.updated_at = datetime.now(timezone.utc)
        db.add(auth)
        db.commit()
    finally:
        if own_db:
            db.close()
