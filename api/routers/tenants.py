"""Tenant list and create."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models.tenant import Tenant
from schemas import CreateTenantRequest, TenantResponse

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _tenant_response(t: Tenant) -> TenantResponse:
    return TenantResponse(
        id=str(t.id),
        name=t.name,
        callback_url=t.callback_url,
        created_at=t.created_at.isoformat() if t.created_at else "",
    )


@router.get("", response_model=list[TenantResponse])
def list_tenants(db: Session = Depends(get_session)) -> list[TenantResponse]:
    rows = db.execute(select(Tenant).order_by(Tenant.created_at.desc())).scalars().all()
    return [_tenant_response(r) for r in rows]


@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(tenant_id: UUID, db: Session = Depends(get_session)) -> TenantResponse:
    row = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Tenant not found."})
    return _tenant_response(row)


@router.post("", response_model=TenantResponse, status_code=201)
def create_tenant(
    body: CreateTenantRequest,
    db: Session = Depends(get_session),
) -> TenantResponse:
    cb = (body.callback_url or "").strip() or None
    t = Tenant(name=body.name.strip(), callback_url=cb)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tenant_response(t)
