"""Approval workflow API routes — token-based approve/reject."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key
from app.database import get_session

# ─── Schemas ───────────────────────────────────────────────────────


class PurchaseOrderCreate(BaseModel):
    po_number: str
    vendor_name: str
    line_items: list[dict] = []
    total_amount: Optional[Decimal] = None
    currency: str = "USD"
    status: str = "open"


class PurchaseOrderResponse(BaseModel):
    id: uuid.UUID
    po_number: str
    vendor_name: str
    line_items: Optional[Any] = None
    total_amount: Optional[Decimal] = None
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VendorCreate(BaseModel):
    canonical_name: str
    aliases: list[str] = []
    tax_id: Optional[str] = None
    payment_terms: Optional[str] = None
    is_approved: bool = False


class VendorUpdate(BaseModel):
    canonical_name: Optional[str] = None
    aliases: Optional[list[str]] = None
    tax_id: Optional[str] = None
    payment_terms: Optional[str] = None
    is_approved: Optional[bool] = None


class VendorResponse(BaseModel):
    id: uuid.UUID
    canonical_name: str
    aliases: Optional[Any] = None
    tax_id: Optional[str] = None
    payment_terms: Optional[str] = None
    is_approved: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Routers ──────────────────────────────────────────────────────

approvals_router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["Approvals"],
)


purchase_orders_router = APIRouter(
    prefix="/api/v1/purchase-orders",
    tags=["Purchase Orders"],
    dependencies=[Depends(get_api_key)],
)


vendors_router = APIRouter(
    prefix="/api/v1/vendors",
    tags=["Vendors"],
    dependencies=[Depends(get_api_key)],
)


# ==============================
# Approval Endpoints (no auth required — token IS the auth)
# ==============================


@approvals_router.get("/{token}/approve")
async def approve_invoice(
    token: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Approve an invoice via approval token."""
    from app.services.approval import redeem_approval_token

    result = await redeem_approval_token(db, token, "approved")
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {
        "message": "Invoice approved successfully",
        "invoice_id": result["invoice_id"],
    }


@approvals_router.get("/{token}/reject")
async def reject_invoice(
    token: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Reject an invoice via approval token."""
    from app.services.approval import redeem_approval_token

    result = await redeem_approval_token(db, token, "rejected")
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {
        "message": "Invoice rejected",
        "invoice_id": result["invoice_id"],
    }


# ==============================
# Purchase Order Endpoints
# ==============================


@purchase_orders_router.post("", response_model=PurchaseOrderResponse, status_code=201)
async def create_purchase_order(
    po_data: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_session),
) -> PurchaseOrderResponse:
    """Create a new purchase order."""
    from app.models.purchase_order import POStatus, PurchaseOrder

    # Check for duplicate PO number
    existing = await db.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == po_data.po_number)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"PO {po_data.po_number} already exists")

    import json

    po = PurchaseOrder(
        po_number=po_data.po_number,
        vendor_name=po_data.vendor_name,
        line_items=json.dumps(po_data.line_items) if po_data.line_items else None,
        total_amount=po_data.total_amount,
        currency=po_data.currency,
        status=po_data.status if hasattr(POStatus, po_data.status.lower()) else POStatus.open,
    )
    db.add(po)
    await db.flush()
    await db.refresh(po)
    return PurchaseOrderResponse.model_validate(po)


@purchase_orders_router.get("", response_model=list[PurchaseOrderResponse])
async def list_purchase_orders(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
) -> list[PurchaseOrderResponse]:
    """List all purchase orders."""
    from app.models.purchase_order import PurchaseOrder

    query = select(PurchaseOrder)
    if status:
        query = query.where(PurchaseOrder.status == status)
    query = query.order_by(PurchaseOrder.created_at.desc())

    result = await db.execute(query)
    pos = result.scalars().all()
    return [PurchaseOrderResponse.model_validate(po) for po in pos]


@purchase_orders_router.get("/{po_id}", response_model=PurchaseOrderResponse)
async def get_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> PurchaseOrderResponse:
    """Get a purchase order by ID."""
    from app.models.purchase_order import PurchaseOrder

    result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
    po = result.scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return PurchaseOrderResponse.model_validate(po)


# ==============================
# Vendor Endpoints
# ==============================


@vendors_router.post("", response_model=VendorResponse, status_code=201)
async def create_vendor(
    vendor_data: VendorCreate,
    db: AsyncSession = Depends(get_session),
) -> VendorResponse:
    """Create a new vendor master record."""
    from app.models.vendor import Vendor

    # Check for duplicate
    existing = await db.execute(
        select(Vendor).where(Vendor.canonical_name == vendor_data.canonical_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Vendor '{vendor_data.canonical_name}' already exists",
        )

    import json

    vendor = Vendor(
        canonical_name=vendor_data.canonical_name,
        aliases=json.dumps(vendor_data.aliases) if vendor_data.aliases else None,
        tax_id=vendor_data.tax_id,
        payment_terms=vendor_data.payment_terms,
        is_approved=vendor_data.is_approved,
    )
    db.add(vendor)
    await db.flush()
    await db.refresh(vendor)
    return VendorResponse.model_validate(vendor)


@vendors_router.get("", response_model=list[VendorResponse])
async def list_vendors(
    db: AsyncSession = Depends(get_session),
) -> list[VendorResponse]:
    """List all vendors."""
    from app.models.vendor import Vendor

    result = await db.execute(select(Vendor).order_by(Vendor.canonical_name.asc()))
    vendors = result.scalars().all()
    return [VendorResponse.model_validate(v) for v in vendors]


@vendors_router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> VendorResponse:
    """Get a vendor by ID."""
    from app.models.vendor import Vendor

    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return VendorResponse.model_validate(vendor)


@vendors_router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(
    vendor_id: uuid.UUID,
    vendor_data: VendorUpdate,
    db: AsyncSession = Depends(get_session),
) -> VendorResponse:
    """Update a vendor record."""
    from app.models.vendor import Vendor

    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    import json

    update_data = vendor_data.model_dump(exclude_unset=True)
    if "aliases" in update_data and update_data["aliases"] is not None:
        update_data["aliases"] = json.dumps(update_data["aliases"])
    elif "aliases" in update_data and update_data["aliases"] is None:
        update_data["aliases"] = None

    for field, value in update_data.items():
        if value is not None:
            setattr(vendor, field, value)

    await db.flush()
    await db.refresh(vendor)
    return VendorResponse.model_validate(vendor)
