import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.tenant import Base


class TenantAuth(Base):
    """Telethon session storage per tenant. 1:1 with Tenant."""

    __tablename__ = "tenant_auth"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    phone = mapped_column(String(32), nullable=True)
    session_string = mapped_column(Text, nullable=True)  # encrypted
    authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    phone_code_hash = mapped_column(String(128), nullable=True)  # temporary: from send_code_request
    code_requested_at = mapped_column(DateTime(timezone=True), nullable=True)  # for resend cooldown
    code_timeout_seconds = mapped_column(Integer, nullable=True)  # SentCode.timeout; resend cooldown
    last_error = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
