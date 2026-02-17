"""
Tenant-scoped send-message endpoint with per-tenant rate limiting.

Rate limiting: see `rate_limit` module docstring. In-memory sliding window per tenant;
returns 429 when exceeded.

Peer resolution: see `peer_resolver` module. Username, user_id, or phone (E.164).
Phone: resolve existing contact, or import via ImportContacts when allow_import_contact;
else PHONE_NOT_IN_CONTACTS or PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import errors as tg_errors
from telethon.tl.types import User

from database import get_session
from models.tenant import Tenant
from models.message import Message
from peer_resolver import resolve_peer
from rate_limit import check_rate_limit
from schemas import (
    ErrorResponse,
    ReadReceiptRequest,
    ReadReceiptResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from telethon_manager import build_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tenants/{tenant_id}",
    tags=["tenant-messages"],
    responses={404: {"description": "Tenant not found"}},
)


def _tenant_or_404(tenant_id: UUID, db: Session) -> Tenant:
    row = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalars().first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Tenant not found."},
        )
    return row


@router.post(
    "/messages/send",
    response_model=SendMessageResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid peer, send failed, PHONE_NOT_IN_CONTACTS, or PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM"},
        401: {"model": ErrorResponse, "description": "Tenant not authorized"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
        429: {"model": ErrorResponse, "description": "Rate limited or Telegram flood wait"},
    },
    summary="Send message",
    description=(
        'Resolve peer ("me", @username, numeric id, or phone E.164), then send text. '
        "Phone: use existing contact or import when allow_import_contact=true; else 400. "
        "Rate-limited per tenant. MVP: imported phone may remain in contacts."
    ),
)
async def send_message(
    tenant_id: UUID,
    body: SendMessageRequest,
    db: Session = Depends(get_session),
) -> SendMessageResponse:
    _tenant_or_404(tenant_id, db)

    allowed, retry_after = check_rate_limit(tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": "Too many send requests. Retry later.",
                "retry_after_seconds": int(retry_after) if retry_after is not None else 60,
            },
        )

    client = build_client(tenant_id, db)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "message": "Tenant not logged in. Use /auth/start and /auth/verify."},
            )

        entity, peer_resolved = await resolve_peer(
            client,
            body.peer,
            allow_import_contact=body.allow_import_contact,
            tenant_id=str(tenant_id),
        )

        try:
            msg = await client.send_message(entity, body.text)
        except tg_errors.FloodWaitError as e:
            logger.warning("send_message: FloodWait tenant=%s seconds=%s", tenant_id, e.seconds)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Telegram rate limit. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e
        except tg_errors.ChatWriteForbiddenError:
            logger.warning("send_message: ChatWriteForbidden tenant=%s peer=%s", tenant_id, peer_resolved)
            raise HTTPException(
                status_code=400,
                detail={"error": "cannot_send", "message": "Cannot write to this peer."},
            ) from None
        except Exception as e:
            logger.exception("send_message: send failed tenant=%s peer=%s", tenant_id, peer_resolved)
            raise HTTPException(
                status_code=400,
                detail={"error": "send_failed", "message": str(e) or type(e).__name__},
            ) from e

        date_str = msg.date.isoformat() if hasattr(msg.date, "isoformat") else str(msg.date)
        
        # Save outbound message to database
        try:
            # Extract username and phone_number from entity
            username: str | None = None
            phone_number: str | None = None
            chat_id: int | None = None
            
            if isinstance(entity, User):
                username = getattr(entity, "username", None)
                if username:
                    username = str(username)
                phone_number = getattr(entity, "phone", None)
                if phone_number:
                    phone_number = str(phone_number)
                chat_id = entity.id
            else:
                # For Chat or Channel, use entity.id as chat_id
                chat_id = getattr(entity, "id", None)
            
            if chat_id is not None:
                message = Message(
                    tenant_id=tenant_id,
                    chat_id=chat_id,
                    message_id=msg.id,
                    username=username,
                    phone_number=phone_number,
                    text=body.text,
                    sender_id=None,  # Outbound messages don't have a sender_id
                    date=msg.date,
                    incoming=False,
                )
                db.add(message)
                db.commit()
        except Exception as e:
            logger.exception("Failed to save outbound message tenant_id=%s error=%s", tenant_id, e)
            # Don't fail the request if saving to DB fails
        
        return SendMessageResponse(
            ok=True,
            peer_resolved=peer_resolved,
            message_id=msg.id,
            date=date_str,
        )
    finally:
        await client.disconnect()


@router.post(
    "/messages/read-receipt",
    response_model=ReadReceiptResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid peer or read-receipt failed"},
        401: {"model": ErrorResponse, "description": "Tenant not authorized"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
        429: {"model": ErrorResponse, "description": "Rate limited or Telegram flood wait"},
    },
    summary="Send read receipt",
    description=(
        "Mark incoming messages in a chat as read up to max_id (sends read receipt to senders). "
        'Peer: "me", @username, numeric user/chat id, or phone E.164. Use chat_id/message_id from '
        "inbound callback payload when acknowledging received messages."
    ),
)
async def send_read_receipt(
    tenant_id: UUID,
    body: ReadReceiptRequest,
    db: Session = Depends(get_session),
) -> ReadReceiptResponse:
    _tenant_or_404(tenant_id, db)

    allowed, retry_after = check_rate_limit(tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": "Too many requests. Retry later.",
                "retry_after_seconds": int(retry_after) if retry_after is not None else 60,
            },
        )

    client = build_client(tenant_id, db)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "message": "Tenant not logged in. Use /auth/start and /auth/verify."},
            )

        entity, _ = await resolve_peer(
            client,
            body.peer,
            allow_import_contact=False,
            tenant_id=str(tenant_id),
        )

        try:
            await client.send_read_acknowledge(entity, max_id=body.max_id)
        except tg_errors.FloodWaitError as e:
            logger.warning("send_read_receipt: FloodWait tenant=%s seconds=%s", tenant_id, e.seconds)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Telegram rate limit. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e
        except Exception as e:
            logger.exception("send_read_receipt: failed tenant=%s peer=%s max_id=%s", tenant_id, body.peer, body.max_id)
            raise HTTPException(
                status_code=400,
                detail={"error": "read_receipt_failed", "message": str(e) or type(e).__name__},
            ) from e

        return ReadReceiptResponse(ok=True, message="Read receipt sent.")
    finally:
        await client.disconnect()
