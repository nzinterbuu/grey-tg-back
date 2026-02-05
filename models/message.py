"""Message table: all sent and received messages per tenant."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.tenant import Base


class Message(Base):
    """Stores inbound and outbound messages with delivery status."""

    __tablename__ = "message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="in = inbound, out = outbound",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="sent",
        comment="Delivery status, e.g. sent, delivered, read, failed",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the message was sent or received",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="When the delivery status was last updated",
    )
    address: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
        comment="Phone number, chat_id, or username of the other party",
    )
