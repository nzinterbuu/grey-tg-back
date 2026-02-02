"""Pydantic schemas for tenant auth API."""

from pydantic import BaseModel, Field


class AuthStartRequest(BaseModel):
    phone: str = Field(..., description="Phone number in international format (e.g. +1234567890)")


class AuthVerifyRequest(BaseModel):
    phone: str = Field(..., description="Same phone used in /auth/start")
    code: str = Field(..., description="OTP code from Telegram (SMS or in-app)")
    password: str | None = Field(
        None,
        description="Telegram 2FA cloud password if two-step verification is enabled",
    )


class AuthStartResponse(BaseModel):
    ok: bool = True
    message: str = "Code sent. Use POST /auth/verify with code."
    delivery: str = "unknown"  # "telegram_app" | "sms" | "call" | "unknown"
    timeout_seconds: int = 0  # cooldown before resend allowed
    hint: str = ""  # user-facing where to look (e.g. "Check Telegram app...")


class AuthVerifyResponse(BaseModel):
    ok: bool = True
    message: str = "Signed in. Session stored."


class LogoutResponse(BaseModel):
    ok: bool = True
    message: str = "Logged out. Session cleared."


class TenantStatusResponse(BaseModel):
    authorized: bool
    phone: str | None = None
    last_error: str | None = None
    cooldown_seconds: int = 0  # 0 = no cooldown; >0 = seconds until resend allowed


class ErrorResponse(BaseModel):
    error: str
    message: str
    retry_after_seconds: int | None = None


class SendMessageRequest(BaseModel):
    peer: str = Field(
        ...,
        description='Target: "me" (Saved Messages), @username, numeric user/chat id, or phone number in E.164 format (e.g. +79001234567)',
    )
    text: str = Field(..., description="Message text to send")
    allow_import_contact: bool = Field(
        True,
        description="If true, import phone as contact when not in contacts to enable sending. If false, return 400 PHONE_NOT_IN_CONTACTS when phone not in contacts.",
    )


class SendMessageResponse(BaseModel):
    ok: bool = True
    peer_resolved: str = Field(..., description="Resolved peer (e.g. me, @user, id)")
    message_id: int = Field(..., description="Telegram message id")
    date: str = Field(..., description="ISO datetime when message was sent")


class ReadReceiptRequest(BaseModel):
    peer: str = Field(
        ...,
        description='Chat to mark as read: "me", @username, numeric user/chat id, or phone E.164. Same as send.',
    )
    max_id: int = Field(..., ge=0, description="Last message ID to mark as read (all messages with id <= max_id)")


class ReadReceiptResponse(BaseModel):
    ok: bool = True
    message: str = Field(default="Read receipt sent.", description="Confirmation message")


class CreateTenantRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    callback_url: str | None = Field(None, max_length=2048)


class TenantResponse(BaseModel):
    id: str
    name: str
    callback_url: str | None
    created_at: str


class CallbackTestResponse(BaseModel):
    ok: bool = True
    message: str = "Test callback sent."
