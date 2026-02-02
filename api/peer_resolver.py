"""
Resolve peer string (username, user_id, or phone) to an entity usable by client.send_message.

- Username: get_entity(username) -> InputPeer.
- User ID: get_entity(user_id) -> InputPeer.
- Phone: normalize E.164; try get_entity(phone) (existing contact). If not found and
  allow_import_contact, import via contacts.ImportContactsRequest; if import returns
  no users -> PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM. If allow_import_contact false
  and not in contacts -> PHONE_NOT_IN_CONTACTS.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException
from telethon import errors as tg_errors
from telethon.tl import functions, types
from telethon.tl.types import User, Chat, Channel

if TYPE_CHECKING:
    from telethon import TelegramClient

logger = logging.getLogger(__name__)


def _normalize_e164(phone: str) -> tuple[str | None, str | None]:
    """Normalize to E.164. Returns (normalized, None) or (None, error_message)."""
    if not phone or not isinstance(phone, str):
        return None, "Phone number is required."
    s = phone.strip()
    stripped = "".join(c for c in s if c.isdigit() or c == "+")
    if not stripped.startswith("+"):
        return None, "Phone must be in E.164 format: +<country><number> (e.g. +79001234567)."
    digits = stripped[1:]
    if not digits or not digits.isdigit():
        return None, "Phone must contain only + followed by digits (E.164)."
    if len(digits) < 10:
        return None, "Phone number too short for E.164."
    return "+" + digits, None


def _is_phone_number(peer: str) -> bool:
    s = peer.strip()
    stripped = "".join(c for c in s if c.isdigit() or c == "+")
    return (
        stripped.startswith("+")
        and len(stripped) > 5
        and all(c.isdigit() or c == "+" for c in stripped)
    )


def _format_peer_resolved(peer_input: str, entity: User | Chat | Channel) -> str:
    if peer_input.strip().lower() in ("me", "self"):
        return "me"
    if _is_phone_number(peer_input):
        normalized, _ = _normalize_e164(peer_input)
        if normalized:
            if isinstance(entity, User) and getattr(entity, "phone", None):
                return entity.phone
            return normalized
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    return str(entity.id)


async def resolve_peer(
    client: "TelegramClient",
    peer: str,
    *,
    allow_import_contact: bool = True,
    tenant_id: str | None = None,
) -> tuple[User | Chat | Channel, str]:
    """
    Resolve peer string to (entity, peer_resolved_display).

    - Username: get_entity(username).
    - User/chat ID: get_entity(id).
    - Phone: E.164 normalize; get_entity(phone) if in contacts; else ImportContacts
      when allow_import_contact, else raise PHONE_NOT_IN_CONTACTS.

    Raises HTTPException(400) for invalid peer, PHONE_NOT_IN_CONTACTS,
    PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM; HTTPException(429) for FloodWait.
    """
    t = tenant_id or "?"
    peer_stripped = peer.strip()

    # --- Phone ---
    if _is_phone_number(peer_stripped):
        normalized, err = _normalize_e164(peer_stripped)
        if err:
            logger.warning("resolve_peer: invalid phone format peer=%r tenant=%s", peer_stripped, t)
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_phone", "message": err},
            )
        assert normalized is not None

        # 1) Try existing contact
        try:
            entity = await client.get_entity(normalized)
            logger.info("resolve_peer: phone found in contacts phone=%s tenant=%s", normalized, t)
            return entity, _format_peer_resolved(peer_stripped, entity)
        except ValueError as e:
            logger.debug("resolve_peer: phone not in contacts, trying import phone=%s tenant=%s %s", normalized, t, e)
            pass
        except tg_errors.FloodWaitError as e:
            logger.warning("resolve_peer: FloodWait resolving phone tenant=%s seconds=%s", t, e.seconds)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Telegram rate limit. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e

        if not allow_import_contact:
            logger.info("resolve_peer: phone not in contacts, import disabled phone=%s tenant=%s", normalized, t)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "PHONE_NOT_IN_CONTACTS",
                    "message": "Phone number not in contacts and contact import is disabled.",
                },
            )

        # 2) Import contact
        client_id = hash(normalized) & 0x7FFF_FFFF_FFFF_FFFF
        inp = types.InputPhoneContact(
            client_id=client_id,
            phone=normalized,
            first_name="",
            last_name="",
        )
        try:
            result = await client(functions.contacts.ImportContactsRequest(contacts=[inp]))
        except tg_errors.FloodWaitError as e:
            logger.warning("resolve_peer: FloodWait on ImportContacts tenant=%s seconds=%s", t, e.seconds)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Telegram rate limit. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e

        users = getattr(result, "users", None) or []
        if not users:
            logger.info(
                "resolve_peer: ImportContacts returned no users (not on Telegram or privacy) phone=%s tenant=%s",
                normalized,
                t,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM",
                    "message": "Number not in contacts and not on Telegram (or has privacy restrictions). Import failed.",
                },
            )
        entity = users[0]
        logger.info("resolve_peer: contact imported for phone=%s user_id=%s tenant=%s", normalized, entity.id, t)
        return entity, _format_peer_resolved(peer_stripped, entity)

    # --- "me" / "self" ---
    if peer_stripped.lower() in ("me", "self"):
        entity = await client.get_me()
        return entity, "me"

    # --- Username or numeric ID ---
    try:
        entity = await client.get_entity(peer_stripped)
        return entity, _format_peer_resolved(peer_stripped, entity)
    except tg_errors.UsernameNotOccupiedError:
        logger.warning("resolve_peer: username not occupied peer=%r tenant=%s", peer_stripped, t)
        raise HTTPException(
            status_code=400,
            detail={"error": "peer_not_found", "message": "Username or peer not found."},
        ) from None
    except (tg_errors.UsernameInvalidError, tg_errors.PeerIdInvalidError, ValueError) as e:
        logger.warning("resolve_peer: invalid peer peer=%r tenant=%s err=%s", peer_stripped, t, e)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_peer", "message": str(e) or "Invalid peer."},
        ) from e
    except tg_errors.FloodWaitError as e:
        logger.warning("resolve_peer: FloodWait peer=%r tenant=%s seconds=%s", peer_stripped, t, e.seconds)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "flood_wait",
                "message": f"Telegram rate limit. Retry after {e.seconds} seconds.",
                "retry_after_seconds": e.seconds,
            },
        ) from e
