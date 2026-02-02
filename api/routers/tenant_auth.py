"""
Tenant-scoped Telegram auth endpoints.

**OTP vs Telegram 2FA password:**
- **OTP (one-time password)**: The numeric code Telegram sends to the user's phone (SMS or in-app)
  when they request login. Proves access to that phone. Required for every login.
- **Telegram 2FA (cloud password)**: An optional extra password set in Telegram
  (Settings → Privacy → Two-Step Verification). If enabled, after entering the OTP
  the user must also provide this password. It is not the SMS code—it's a separate
  secret only the user knows.
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import errors as tg_errors
from telethon.tl import functions, types

logger = logging.getLogger(__name__)

from callback_dispatch import start_dispatcher, stop_dispatcher
from database import get_session
from models.tenant import Tenant
from models.tenant_auth import TenantAuth
from schemas import (
    AuthStartRequest,
    AuthStartResponse,
    AuthVerifyRequest,
    AuthVerifyResponse,
    ErrorResponse,
    LogoutResponse,
    TenantStatusResponse,
)
from telethon_manager import build_client, clear_session, save_session, set_last_error

router = APIRouter(
    prefix="/tenants/{tenant_id}",
    tags=["tenant-auth"],
    responses={404: {"description": "Tenant not found"}},
)


def _normalize_e164(phone: str) -> tuple[str | None, str | None]:
    """
    Normalize phone to strict E.164 (+<country><number>).
    Reject if missing + or contains invalid chars (spaces/dashes allowed but stripped).
    Returns (normalized, None) or (None, error_message).
    """
    if not phone or not isinstance(phone, str):
        return None, "Phone number is required."
    s = phone.strip()
    # Strip spaces, dashes, parentheses for validation
    stripped = "".join(c for c in s if c.isdigit() or c == "+")
    if not stripped.startswith("+"):
        return None, "Phone must be in E.164 format: +<country><number> (e.g. +79001234567)."
    digits = stripped[1:]
    if not digits or not digits.isdigit():
        return None, "Phone must contain only + followed by digits (E.164)."
    if len(digits) < 10:
        return None, "Phone number too short for E.164."
    return "+" + digits, None


def _tenant_or_404(tenant_id: UUID, db: Session) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalars().first()
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Tenant not found."},
        )
    return tenant


def _normalize_phone_for_compare(phone: str) -> str:
    """E.164-normalize for comparison only; no validation."""
    s = "".join(c for c in (phone or "") if c.isdigit() or c == "+")
    return s if s.startswith("+") else "+" + s if s.isdigit() else ""


def _mask_hash(h: str | None) -> str:
    if not h or len(h) < 8:
        return "***"
    return f"{h[:4]}...{h[-4:]}"


def _cooldown_seconds(auth: TenantAuth) -> int:
    """Seconds until resend allowed; 0 if no cooldown."""
    if not auth.code_requested_at or not auth.code_timeout_seconds:
        return 0
    end = auth.code_requested_at.timestamp() + auth.code_timeout_seconds
    now = datetime.now(timezone.utc).timestamp()
    return max(0, int(end - now))


def _sent_code_diagnostics(
    result, tenant_id: UUID
) -> tuple[str, int, str]:
    """
    Log full SentCode diagnostics and return (delivery, timeout_seconds, hint).
    delivery: "telegram_app" | "sms" | "call" | "unknown"
    """
    t = getattr(result, "type", None)
    next_t = getattr(result, "next_type", None)
    timeout = getattr(result, "timeout", None)
    timeout = int(timeout) if timeout is not None else 0
    h = getattr(result, "phone_code_hash", "") or ""
    type_name = type(t).__name__ if t else "unknown"
    next_name = type(next_t).__name__ if next_t else "none"
    logger.info(
        "Tenant %s: SentCode diagnostics type=%s next_type=%s timeout=%s phone_code_hash=%s",
        tenant_id,
        type_name,
        next_name,
        timeout,
        _mask_hash(h),
    )
    delivery = "unknown"
    if t:
        n = type_name.lower()
        if "app" in n:
            delivery = "telegram_app"
        elif "sms" in n or "sms" in type_name:
            delivery = "sms"
        elif "call" in n:
            delivery = "call"
    if delivery == "telegram_app":
        hint = "Check Telegram app (Saved Messages / Telegram login message). App notification requires Telegram logged in on another device and online."
    elif delivery == "sms":
        hint = "Check your phone SMS messages for the login code."
    elif delivery == "call":
        hint = "Answer the incoming phone call to get the code."
    else:
        hint = "Check your Telegram app or phone messages for the login code."
    return delivery, timeout, hint


@router.get(
    "/status",
    response_model=TenantStatusResponse,
    summary="Tenant auth status",
    description="Returns authorized, phone, last_error, and cooldown_seconds until resend allowed. Tenant-isolated.",
)
def get_status(
    tenant_id: UUID,
    db: Session = Depends(get_session),
) -> TenantStatusResponse:
    _tenant_or_404(tenant_id, db)
    row = (
        db.execute(select(TenantAuth).where(TenantAuth.tenant_id == tenant_id))
        .scalars()
        .first()
    )
    if not row:
        return TenantStatusResponse(authorized=False, phone=None, last_error=None, cooldown_seconds=0)
    return TenantStatusResponse(
        authorized=bool(row.authorized),
        phone=row.phone,
        last_error=row.last_error,
        cooldown_seconds=_cooldown_seconds(row),
    )


@router.post(
    "/auth/start",
    response_model=AuthStartResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
        429: {"model": ErrorResponse, "description": "Flood wait"},
    },
    summary="Send OTP",
    description="Sends login code to phone. Use same phone + code in POST /auth/verify. Tenant-isolated.",
)
async def auth_start(
    tenant_id: UUID,
    body: AuthStartRequest,
    db: Session = Depends(get_session),
) -> AuthStartResponse:
    """
    Auth state: IDLE -> WAIT_CODE
    Sends OTP via Telegram. Returns delivery, timeout_seconds, hint for UI.
    """
    _tenant_or_404(tenant_id, db)
    normalized_phone, err = _normalize_e164(body.phone)
    if err:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_phone", "message": err},
        )
    assert normalized_phone is not None

    client = build_client(tenant_id, db)
    try:
        await client.connect()
        try:
            result = await client.send_code_request(normalized_phone)
        except tg_errors.FloodWaitError as e:
            set_last_error(tenant_id, f"Flood wait: retry after {e.seconds} seconds", db)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Too many attempts. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e
        except tg_errors.PhoneNumberInvalidError:
            set_last_error(tenant_id, "Invalid phone number", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_phone", "message": "Invalid phone number."},
            ) from None
        except tg_errors.PhoneNumberBannedError:
            set_last_error(tenant_id, "Phone number banned", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "phone_banned", "message": "This phone number is banned by Telegram."},
            ) from None
        except tg_errors.PhoneNumberFloodError:
            set_last_error(tenant_id, "Phone number flood", db)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "phone_flood",
                    "message": "Too many attempts for this phone. Wait before retrying.",
                    "retry_after_seconds": 60,
                },
            ) from None
        except tg_errors.AuthRestartError:
            set_last_error(tenant_id, "Auth restart", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "auth_restart", "message": "Auth was restarted. Try again."},
            ) from None
        except tg_errors.SendCodeUnavailableError:
            set_last_error(tenant_id, "Send code unavailable", db)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "send_code_unavailable",
                    "message": "Code delivery unavailable. All options (app, SMS, call) exhausted. Try again later.",
                },
            ) from None
        except Exception as e:
            msg = str(e) or type(e).__name__
            set_last_error(tenant_id, msg, db)
            raise HTTPException(
                status_code=400,
                detail={"error": "send_code_failed", "message": msg},
            ) from e

        # SentCodeSuccess = already logged in; unexpected here
        if isinstance(result, types.auth.SentCodeSuccess):
            set_last_error(tenant_id, "Already logged in", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "already_logged_in", "message": "Session already authorized."},
            ) from None

        delivery, timeout_seconds, hint = _sent_code_diagnostics(result, tenant_id)

        await save_session(tenant_id, client, db, authorized=False)
        auth = (
            db.execute(select(TenantAuth).where(TenantAuth.tenant_id == tenant_id)).scalars().first()
        )
        if not auth:
            auth = TenantAuth(tenant_id=tenant_id)
            db.add(auth)
        auth.phone_code_hash = result.phone_code_hash
        auth.phone = normalized_phone
        auth.code_requested_at = datetime.now(timezone.utc)
        auth.code_timeout_seconds = timeout_seconds or 0
        db.commit()

        await asyncio.sleep(1.0)
        return AuthStartResponse(
            ok=True,
            message="Code sent. Use POST /auth/verify with code.",
            delivery=delivery,
            timeout_seconds=timeout_seconds or 0,
            hint=hint,
        )
    finally:
        await client.disconnect()


@router.post(
    "/auth/verify",
    response_model=AuthVerifyResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid code or other error"},
        403: {"model": ErrorResponse, "description": "2FA required"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
        429: {"model": ErrorResponse, "description": "Flood wait"},
    },
    summary="Verify OTP and complete sign-in",
    description="Provide OTP from Telegram. If 2FA is enabled, include password. Tenant-isolated.",
)
async def auth_verify(
    tenant_id: UUID,
    body: AuthVerifyRequest,
    db: Session = Depends(get_session),
) -> AuthVerifyResponse:
    """
    Auth state: WAIT_CODE -> (WAIT_2FA | READY)
    
    Reuses the SAME TelegramClient session from auth_start (which contains phone_code_hash).
    After successful sign-in, saves the fully authorized session.
    """
    tenant = _tenant_or_404(tenant_id, db)
    normalized_phone, err = _normalize_e164(body.phone)
    if err:
        raise HTTPException(status_code=400, detail={"error": "invalid_phone", "message": err})
    assert normalized_phone is not None

    # Check that we have a session with phone_code_hash
    auth = db.execute(select(TenantAuth).where(TenantAuth.tenant_id == tenant_id)).scalars().first()
    if not auth or not auth.session_string:
        logger.warning(f"Tenant {tenant_id}: No session found (auth exists: {auth is not None}, session exists: {auth.session_string if auth else None})")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_code_request",
                "message": "No code request found. Call /auth/start first.",
            },
        )
    
    # Verify phone number matches (both E.164)
    stored = _normalize_phone_for_compare(auth.phone or "")
    if stored and stored != normalized_phone:
        logger.warning("Tenant %s: Phone mismatch stored=%s provided=%s", tenant_id, auth.phone, body.phone)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "phone_mismatch",
                "message": f"Phone number mismatch. Use the same phone number ({auth.phone}) that was used in /auth/start.",
            },
        )
    
    logger.info(f"Tenant {tenant_id}: Verifying code for phone {normalized_phone} (state: WAIT_CODE -> WAIT_2FA/READY)")
    
    # CRITICAL: Reuse the SAME session from auth_start
    # This session contains the phone_code_hash internally from send_code_request()
    client = build_client(tenant_id, db)
    try:
        await client.connect()
        try:
            # Get phone_code_hash from DB as backup, but the session should already have it
            phone_code_hash = auth.phone_code_hash
            if not phone_code_hash:
                logger.warning(f"Tenant {tenant_id}: phone_code_hash not in DB, relying on session state")
            
            logger.debug(f"Tenant {tenant_id}: Calling sign_in with phone={normalized_phone}, code={body.code[:2]}**, hash={phone_code_hash[:10] if phone_code_hash else 'from_session'}...")
            await client.sign_in(normalized_phone, code=body.code, phone_code_hash=phone_code_hash)
        except tg_errors.SessionPasswordNeededError:
            # State: WAIT_CODE -> WAIT_2FA
            # Code was valid, but 2FA password required
            # Save session state (still contains phone_code_hash) so we can retry with password
            await save_session(tenant_id, client, db, authorized=False)
            
            if not body.password:
                set_last_error(tenant_id, "2FA required", db)
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "2fa_required",
                        "message": "Two-step verification is enabled. Provide 'password' in the request body.",
                    },
                )
            try:
                # State: WAIT_2FA -> READY
                await client.sign_in(password=body.password)
                # Save fully authorized session
                await save_session(tenant_id, client, db, authorized=True)
                # Clear phone_code_hash after successful sign-in
                if auth:
                    auth.phone_code_hash = None
                    db.commit()
                logger.info(f"Tenant {tenant_id}: 2FA sign-in successful (state: READY)")
                # Start dispatcher if callback_url is set
                if tenant.callback_url:
                    await start_dispatcher(tenant_id, tenant.callback_url)
                # Return early - don't fall through to the normal success path
                return AuthVerifyResponse()
            except tg_errors.PasswordHashInvalidError:
                set_last_error(tenant_id, "Invalid 2FA password", db)
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_password", "message": "Invalid 2FA password."},
                ) from None
            except Exception as e:
                msg = str(e) or type(e).__name__
                set_last_error(tenant_id, msg, db)
                raise HTTPException(status_code=400, detail={"error": "sign_in_failed", "message": msg}) from e
        except tg_errors.PhoneCodeInvalidError as e:
            # Clear phone_code_hash on invalid code
            logger.warning(f"Tenant {tenant_id}: PhoneCodeInvalidError - {e}")
            if auth:
                auth.phone_code_hash = None
                db.commit()
            set_last_error(tenant_id, "Invalid code", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_code", "message": "Invalid OTP code. Please check the code and try again."},
            ) from None
        except tg_errors.PhoneCodeExpiredError as e:
            # Clear phone_code_hash on expired code
            logger.warning(f"Tenant {tenant_id}: PhoneCodeExpiredError - {e}")
            if auth:
                auth.phone_code_hash = None
                db.commit()
            set_last_error(tenant_id, "Code expired", db)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "code_expired",
                    "message": "OTP code has expired. Telegram may expire codes immediately if they detect automated usage. Please request a new code by clicking 'Start OTP' again and enter it immediately after receiving it in your Telegram app.",
                },
            ) from None
        except tg_errors.FloodWaitError as e:
            set_last_error(tenant_id, f"Flood wait: retry after {e.seconds} seconds", db)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Too many attempts. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e
        except Exception as e:
            # Log the actual exception for debugging
            logger.error(f"Tenant {tenant_id}: Unexpected error during sign_in: {type(e).__name__}: {e}", exc_info=True)
            # Clear phone_code_hash on other errors
            if auth:
                auth.phone_code_hash = None
                db.commit()
            msg = str(e) or type(e).__name__
            set_last_error(tenant_id, msg, db)
            # Check if it's actually a code-related error wrapped in a generic exception
            error_msg = msg
            if "expired" in msg.lower() or "expire" in msg.lower():
                error_msg = "OTP code has expired. Request a new one via /auth/start."
            elif "invalid" in msg.lower() and "code" in msg.lower():
                error_msg = "Invalid OTP code. Please check the code and try again."
            raise HTTPException(status_code=400, detail={"error": "sign_in_failed", "message": error_msg}) from e

        # Save fully authorized session (state: READY)
        await save_session(tenant_id, client, db, authorized=True)
        
        # Clear phone_code_hash after successful sign-in (no longer needed)
        if auth:
            auth.phone_code_hash = None
            db.commit()
        
        logger.info(f"Tenant {tenant_id}: Sign-in successful (state: READY)")
        if tenant.callback_url:
            await start_dispatcher(tenant_id, tenant.callback_url)
    finally:
        await client.disconnect()
    return AuthVerifyResponse()


@router.post(
    "/auth/resend",
    response_model=AuthStartResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No code request / invalid"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
        429: {"model": ErrorResponse, "description": "Cooldown or flood wait"},
    },
    summary="Resend OTP",
    description="Resends login code using ResendCodeRequest. Respects cooldown (timeout). Use after /auth/start.",
)
async def auth_resend(
    tenant_id: UUID,
    db: Session = Depends(get_session),
) -> AuthStartResponse:
    """
    Resend code for the same phone as /auth/start. Uses ResendCodeRequest(phone, phone_code_hash).
    Fails with 429 if still in cooldown (timeout) or FloodWait.
    """
    _tenant_or_404(tenant_id, db)
    auth = db.execute(select(TenantAuth).where(TenantAuth.tenant_id == tenant_id)).scalars().first()
    if not auth or not auth.session_string or not auth.phone_code_hash or not auth.phone:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_code_request",
                "message": "No code request found. Call POST /auth/start first.",
            },
        )
    cd = _cooldown_seconds(auth)
    if cd > 0:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "cooldown",
                "message": f"Wait {cd} seconds before resending.",
                "retry_after_seconds": cd,
            },
        )
    phone = auth.phone
    client = build_client(tenant_id, db)
    try:
        await client.connect()
        try:
            result = await client(functions.auth.ResendCodeRequest(phone, auth.phone_code_hash))
        except tg_errors.FloodWaitError as e:
            set_last_error(tenant_id, f"Flood wait: retry after {e.seconds} seconds", db)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "flood_wait",
                    "message": f"Too many attempts. Retry after {e.seconds} seconds.",
                    "retry_after_seconds": e.seconds,
                },
            ) from e
        except tg_errors.PhoneCodeExpiredError:
            set_last_error(tenant_id, "Code expired", db)
            auth.phone_code_hash = None
            db.commit()
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "code_expired",
                    "message": "Code expired. Call POST /auth/start again.",
                },
            ) from None
        except tg_errors.SendCodeUnavailableError:
            set_last_error(tenant_id, "Send code unavailable", db)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "send_code_unavailable",
                    "message": "Resend unavailable. All delivery options exhausted. Try /auth/start later.",
                },
            ) from None
        except Exception as e:
            msg = str(e) or type(e).__name__
            set_last_error(tenant_id, msg, db)
            raise HTTPException(
                status_code=400,
                detail={"error": "resend_failed", "message": msg},
            ) from e

        if isinstance(result, types.auth.SentCodeSuccess):
            set_last_error(tenant_id, "Already logged in", db)
            raise HTTPException(
                status_code=400,
                detail={"error": "already_logged_in", "message": "Session already authorized."},
            ) from None

        delivery, timeout_seconds, hint = _sent_code_diagnostics(result, tenant_id)
        await save_session(tenant_id, client, db, authorized=False)
        auth.phone_code_hash = result.phone_code_hash
        auth.code_requested_at = datetime.now(timezone.utc)
        auth.code_timeout_seconds = timeout_seconds or 0
        db.commit()
        await asyncio.sleep(1.0)
        return AuthStartResponse(
            ok=True,
            message="Code resent.",
            delivery=delivery,
            timeout_seconds=timeout_seconds or 0,
            hint=hint,
        )
    finally:
        await client.disconnect()


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={404: {"model": ErrorResponse, "description": "Tenant not found"}},
    summary="Log out and clear session",
    description="Calls client.log_out() and clears stored session for this tenant. Tenant-isolated.",
)
async def logout(
    tenant_id: UUID,
    db: Session = Depends(get_session),
) -> LogoutResponse:
    _tenant_or_404(tenant_id, db)
    await stop_dispatcher(tenant_id)
    client = build_client(tenant_id, db)
    try:
        await client.connect()
        if await client.is_user_authorized():
            try:
                await client.log_out()
            except Exception:
                pass
    finally:
        await client.disconnect()
    clear_session(tenant_id, db)
    return LogoutResponse()
