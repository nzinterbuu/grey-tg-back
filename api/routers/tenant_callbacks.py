"""Tenant callback test endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from callback_dispatch import send_test_callback
from database import get_session
from models.tenant import Tenant
from schemas import CallbackTestResponse, ErrorResponse

router = APIRouter(
    prefix="/tenants/{tenant_id}",
    tags=["tenant-callbacks"],
    responses={404: {"description": "Tenant not found"}},
)


@router.post(
    "/callback/test",
    response_model=CallbackTestResponse,
    responses={
        400: {"model": ErrorResponse, "description": "No callback_url or send failed"},
        404: {"model": ErrorResponse, "description": "Tenant not found"},
    },
    summary="Send test callback",
    description="POST a test message payload to the tenant's callback_url.",
)
async def callback_test(
    tenant_id: UUID,
    db: Session = Depends(get_session),
) -> CallbackTestResponse:
    row = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalars().first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Tenant not found."},
        )
    if not row.callback_url or not row.callback_url.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "no_callback_url", "message": "Tenant has no callback_url set."},
        )
    ok, err = await send_test_callback(tenant_id, row.callback_url.strip())
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={"error": "callback_failed", "message": err or "Test callback failed."},
        )
    return CallbackTestResponse()
