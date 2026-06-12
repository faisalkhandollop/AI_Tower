"""
api/billing.py — Billing & Revenue (Phase 2)
=============================================
Invoice generation, payment tracking, subscription billing and
revenue/MRR/ARR reporting.

Routes:
    # ── Invoices ───────────────────────────────────────────────────────────
    POST   /billing/invoices                 → Create / generate an invoice (Super Admin)
    GET    /billing/invoices                 → List invoices, filterable (Super Admin)
    GET    /billing/invoices/my              → Current org's invoices (Org Admin)
    GET    /billing/invoices/{invoice_id}    → Get invoice detail
    PUT    /billing/invoices/{invoice_id}    → Update invoice (status, due date, notes, tax)
    POST   /billing/invoices/{invoice_id}/void → Void an invoice (Super Admin)

    # ── Payments ───────────────────────────────────────────────────────────
    POST   /billing/payments                 → Record a payment (Super Admin)
    GET    /billing/payments                 → List payments, filterable (Super Admin)
    GET    /billing/payments/my              → Current org's payments (Org Admin)
    GET    /billing/payments/{payment_id}    → Get payment detail

    # ── Dashboard ──────────────────────────────────────────────────────────
    GET    /billing/dashboard                → MRR, ARR, revenue, pending payments (Super Admin)
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import Organization, User, UserTeam
from app.db.models_enterprise import Subscription, SubscriptionPlan
from app.db.models_billing import (
    Invoice,
    Payment,
    INVOICE_STATUSES,
    PAYMENT_STATUSES,
    PAYMENT_METHODS,
)
from app.services.auth import get_current_user
from app.services.rbac import require_super_admin, require_org_admin

router = APIRouter(prefix="/billing", tags=["Billing & Revenue"])


# =============================================================================
# Pydantic Schemas
# =============================================================================

class LineItem(BaseModel):
    description: str             = Field(..., min_length=1, max_length=500)
    quantity:    float           = Field(1, gt=0)
    unit_price:  float           = Field(..., ge=0)


class CreateInvoiceRequest(BaseModel):
    organization_id: str
    subscription_id: Optional[str]      = None
    currency:         str               = Field("USD", min_length=3, max_length=3)
    line_items:       List[LineItem]    = Field(..., min_length=1)
    tax:              float             = Field(0, ge=0)
    period_start:     Optional[datetime] = None
    period_end:       Optional[datetime] = None
    due_date:         Optional[datetime] = None
    status:           str               = Field("draft", description="draft | sent")
    notes:            Optional[str]      = None


class UpdateInvoiceRequest(BaseModel):
    status:    Optional[str]      = None
    due_date:  Optional[datetime] = None
    tax:       Optional[float]    = Field(None, ge=0)
    notes:     Optional[str]      = None


class RecordPaymentRequest(BaseModel):
    organization_id:       str
    invoice_id:            Optional[str]      = None
    amount:                float              = Field(..., gt=0)
    currency:              str                = Field("USD", min_length=3, max_length=3)
    payment_method:        str                = Field("manual", description="card | bank_transfer | upi | paypal | manual | other")
    status:                str                = Field("succeeded", description="pending | succeeded | failed | refunded")
    transaction_reference: Optional[str]      = None
    paid_at:               Optional[datetime] = None
    notes:                 Optional[str]      = None


# =============================================================================
# Serializers
# =============================================================================

def _invoice_dict(inv: Invoice, db: Session) -> dict:
    org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
    return {
        "id":               str(inv.id),
        "organization_id":  str(inv.organization_id),
        "organization_name": org.name if org else None,
        "subscription_id":  str(inv.subscription_id) if inv.subscription_id else None,
        "invoice_number":   inv.invoice_number,
        "status":           inv.status,
        "currency":         inv.currency,
        "line_items":       inv.line_items or [],
        "subtotal":         float(inv.subtotal),
        "tax":              float(inv.tax),
        "total":            float(inv.total),
        "amount_paid":      float(inv.amount_paid),
        "amount_due":       float(inv.amount_due),
        "period_start":     inv.period_start.isoformat() if inv.period_start else None,
        "period_end":       inv.period_end.isoformat()   if inv.period_end   else None,
        "issue_date":       inv.issue_date.isoformat()   if inv.issue_date   else None,
        "due_date":         inv.due_date.isoformat()     if inv.due_date     else None,
        "paid_at":          inv.paid_at.isoformat()      if inv.paid_at      else None,
        "voided_at":        inv.voided_at.isoformat()    if inv.voided_at    else None,
        "notes":            inv.notes,
        "created_at":       inv.created_at.isoformat() if inv.created_at else None,
        "updated_at":       inv.updated_at.isoformat() if inv.updated_at else None,
    }


def _payment_dict(p: Payment, db: Session) -> dict:
    org = db.query(Organization).filter(Organization.id == p.organization_id).first()
    inv = db.query(Invoice).filter(Invoice.id == p.invoice_id).first() if p.invoice_id else None
    return {
        "id":                    str(p.id),
        "organization_id":       str(p.organization_id),
        "organization_name":     org.name if org else None,
        "invoice_id":            str(p.invoice_id) if p.invoice_id else None,
        "invoice_number":        inv.invoice_number if inv else None,
        "amount":                float(p.amount),
        "currency":              p.currency,
        "payment_method":        p.payment_method,
        "status":                p.status,
        "transaction_reference": p.transaction_reference,
        "notes":                 p.notes,
        "paid_at":               p.paid_at.isoformat()  if p.paid_at  else None,
        "created_at":            p.created_at.isoformat() if p.created_at else None,
        "updated_at":            p.updated_at.isoformat() if p.updated_at else None,
    }


# =============================================================================
# Helpers
# =============================================================================

def _validate_invoice_status(s: str) -> str:
    if s not in INVOICE_STATUSES:
        raise HTTPException(400, f"Invalid invoice status '{s}'. Must be one of: {', '.join(INVOICE_STATUSES)}")
    return s


def _validate_payment_status(s: str) -> str:
    if s not in PAYMENT_STATUSES:
        raise HTTPException(400, f"Invalid payment status '{s}'. Must be one of: {', '.join(PAYMENT_STATUSES)}")
    return s


def _validate_payment_method(m: str) -> str:
    if m not in PAYMENT_METHODS:
        raise HTTPException(400, f"Invalid payment method '{m}'. Must be one of: {', '.join(PAYMENT_METHODS)}")
    return m


def _get_invoice(invoice_id: str, db: Session) -> Invoice:
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, f"Invoice '{invoice_id}' not found")
    return inv


def _get_payment(payment_id: str, db: Session) -> Payment:
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404, f"Payment '{payment_id}' not found")
    return p


def _generate_invoice_number(db: Session) -> str:
    now = datetime.now(timezone.utc)
    prefix = f"INV-{now.strftime('%Y%m')}-"
    count_this_month = (
        db.query(func.count(Invoice.id))
        .filter(Invoice.invoice_number.like(f"{prefix}%"))
        .scalar() or 0
    )
    seq = count_this_month + 1
    candidate = f"{prefix}{seq:05d}"
    # guard against rare collisions
    while db.query(Invoice).filter(Invoice.invoice_number == candidate).first():
        seq += 1
        candidate = f"{prefix}{seq:05d}"
    return candidate


def _recalc_invoice_payment_state(inv: Invoice, db: Session) -> None:
    """Recompute amount_paid / amount_due / status from succeeded payments."""
    total_paid = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.invoice_id == inv.id, Payment.status == "succeeded")
        .scalar() or 0
    )
    inv.amount_paid = total_paid
    inv.amount_due  = max(float(inv.total) - float(total_paid), 0)

    if inv.status in ("void",):
        return  # voided invoices keep their status regardless of payments

    if float(inv.amount_due) <= 0 and float(inv.total) > 0:
        inv.status = "paid"
        if not inv.paid_at:
            inv.paid_at = datetime.now(timezone.utc)
    elif float(total_paid) > 0:
        inv.status = "partially_paid"
    elif inv.status == "paid":
        # payment(s) reversed/refunded back to zero
        inv.status = "sent"
        inv.paid_at = None


def _get_org_id_for_user(current_user: User, db: Session) -> uuid.UUID:
    team_row = db.query(UserTeam).filter(UserTeam.user_id == current_user.id).first()
    if not team_row:
        raise HTTPException(404, "No organization found for your account")
    return team_row.org_id


# =============================================================================
# INVOICE ENDPOINTS
# =============================================================================

@router.post(
    "/invoices",
    status_code=201,
    summary="Create / generate an invoice (Super Admin only)",
)
def create_invoice(
    payload: CreateInvoiceRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    org = db.query(Organization).filter(Organization.id == payload.organization_id).first()
    if not org:
        raise HTTPException(404, f"Organization '{payload.organization_id}' not found")

    if payload.subscription_id:
        sub = db.query(Subscription).filter(Subscription.id == payload.subscription_id).first()
        if not sub:
            raise HTTPException(404, f"Subscription '{payload.subscription_id}' not found")
        if str(sub.organization_id) != str(org.id):
            raise HTTPException(400, "Subscription does not belong to the given organization")

    status_val = _validate_invoice_status(payload.status)
    if status_val not in ("draft", "sent"):
        raise HTTPException(400, "New invoices may only be created with status 'draft' or 'sent'")

    subtotal = sum(li.quantity * li.unit_price for li in payload.line_items)
    total    = subtotal + payload.tax

    now = datetime.now(timezone.utc)
    inv = Invoice(
        id=uuid.uuid4(),
        organization_id=org.id,
        subscription_id=payload.subscription_id,
        invoice_number=_generate_invoice_number(db),
        status=status_val,
        currency=payload.currency.upper(),
        line_items=[li.model_dump() for li in payload.line_items],
        subtotal=subtotal,
        tax=payload.tax,
        total=total,
        amount_paid=0,
        amount_due=total,
        period_start=payload.period_start,
        period_end=payload.period_end,
        issue_date=now,
        due_date=payload.due_date or (now + timedelta(days=15)),
        notes=payload.notes,
        created_by=_sa.get("sub"),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return _invoice_dict(inv, db)


@router.get(
    "/invoices",
    summary="List invoices, filterable (Super Admin only)",
)
def list_invoices(
    organization_id: Optional[str] = Query(None),
    status:          Optional[str] = Query(None, description="Filter by invoice status"),
    overdue_only:    bool           = Query(False, description="Only invoices past their due date and unpaid"),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    q = db.query(Invoice)

    if organization_id:
        q = q.filter(Invoice.organization_id == organization_id)
    if status:
        _validate_invoice_status(status)
        q = q.filter(Invoice.status == status)
    if overdue_only:
        now = datetime.now(timezone.utc)
        q = q.filter(
            Invoice.due_date < now,
            Invoice.status.in_(["sent", "partially_paid", "overdue"]),
        )

    total = q.count()
    invoices = q.order_by(Invoice.issue_date.desc()).offset(offset).limit(limit).all()

    return {
        "invoices": [_invoice_dict(inv, db) for inv in invoices],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get(
    "/invoices/my",
    summary="List current organization's invoices (Org Admin)",
)
def get_my_invoices(
    status: Optional[str] = Query(None),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _oa: dict = Depends(require_org_admin),
):
    org_id = _get_org_id_for_user(current_user, db)

    q = db.query(Invoice).filter(Invoice.organization_id == org_id)
    if status:
        _validate_invoice_status(status)
        q = q.filter(Invoice.status == status)

    total = q.count()
    invoices = q.order_by(Invoice.issue_date.desc()).offset(offset).limit(limit).all()

    return {
        "invoices": [_invoice_dict(inv, db) for inv in invoices],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get(
    "/invoices/{invoice_id}",
    summary="Get invoice detail",
)
def get_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _invoice_dict(_get_invoice(invoice_id, db), db)


@router.put(
    "/invoices/{invoice_id}",
    summary="Update invoice — status, due date, tax, notes (Super Admin only)",
)
def update_invoice(
    invoice_id: str,
    payload: UpdateInvoiceRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    inv = _get_invoice(invoice_id, db)

    if inv.status == "void":
        raise HTTPException(400, "Cannot update a voided invoice")

    if payload.status is not None:
        new_status = _validate_invoice_status(payload.status)
        if new_status == "void":
            raise HTTPException(400, "Use POST /billing/invoices/{invoice_id}/void to void an invoice")
        inv.status = new_status

    if payload.due_date is not None:
        inv.due_date = payload.due_date

    if payload.tax is not None:
        inv.tax   = payload.tax
        inv.total = float(inv.subtotal) + float(payload.tax)
        _recalc_invoice_payment_state(inv, db)

    if payload.notes is not None:
        inv.notes = payload.notes

    db.commit()
    db.refresh(inv)
    return _invoice_dict(inv, db)


@router.post(
    "/invoices/{invoice_id}/void",
    summary="Void an invoice — no further payments expected (Super Admin only)",
)
def void_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    inv = _get_invoice(invoice_id, db)

    if inv.status == "paid":
        raise HTTPException(400, "Cannot void a fully paid invoice. Issue a refund instead.")
    if inv.status == "void":
        raise HTTPException(400, "Invoice is already void")

    inv.status    = "void"
    inv.voided_at = datetime.now(timezone.utc)
    inv.amount_due = 0

    db.commit()
    db.refresh(inv)
    return {"message": "Invoice voided", "invoice": _invoice_dict(inv, db)}


# =============================================================================
# PAYMENT ENDPOINTS
# =============================================================================

@router.post(
    "/payments",
    status_code=201,
    summary="Record a payment (Super Admin only)",
)
def record_payment(
    payload: RecordPaymentRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    org = db.query(Organization).filter(Organization.id == payload.organization_id).first()
    if not org:
        raise HTTPException(404, f"Organization '{payload.organization_id}' not found")

    inv = None
    if payload.invoice_id:
        inv = _get_invoice(payload.invoice_id, db)
        if str(inv.organization_id) != str(org.id):
            raise HTTPException(400, "Invoice does not belong to the given organization")
        if inv.status == "void":
            raise HTTPException(400, "Cannot record a payment against a voided invoice")

    method = _validate_payment_method(payload.payment_method)
    pstatus = _validate_payment_status(payload.status)

    payment = Payment(
        id=uuid.uuid4(),
        organization_id=org.id,
        invoice_id=inv.id if inv else None,
        amount=payload.amount,
        currency=payload.currency.upper(),
        payment_method=method,
        status=pstatus,
        transaction_reference=payload.transaction_reference,
        notes=payload.notes,
        paid_at=payload.paid_at or (datetime.now(timezone.utc) if pstatus == "succeeded" else None),
        recorded_by=_sa.get("sub"),
    )
    db.add(payment)

    if inv:
        db.flush()  # ensure payment row visible to recalculation query
        _recalc_invoice_payment_state(inv, db)

    db.commit()
    db.refresh(payment)

    result = {"payment": _payment_dict(payment, db)}
    if inv:
        db.refresh(inv)
        result["invoice"] = _invoice_dict(inv, db)
    return result


@router.get(
    "/payments",
    summary="List payments, filterable (Super Admin only)",
)
def list_payments(
    organization_id: Optional[str] = Query(None),
    invoice_id:      Optional[str] = Query(None),
    status:          Optional[str] = Query(None),
    payment_method:  Optional[str] = Query(None),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    q = db.query(Payment)

    if organization_id:
        q = q.filter(Payment.organization_id == organization_id)
    if invoice_id:
        q = q.filter(Payment.invoice_id == invoice_id)
    if status:
        _validate_payment_status(status)
        q = q.filter(Payment.status == status)
    if payment_method:
        _validate_payment_method(payment_method)
        q = q.filter(Payment.payment_method == payment_method)

    total = q.count()
    payments = q.order_by(Payment.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "payments": [_payment_dict(p, db) for p in payments],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get(
    "/payments/my",
    summary="List current organization's payments (Org Admin)",
)
def get_my_payments(
    status: Optional[str] = Query(None),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _oa: dict = Depends(require_org_admin),
):
    org_id = _get_org_id_for_user(current_user, db)

    q = db.query(Payment).filter(Payment.organization_id == org_id)
    if status:
        _validate_payment_status(status)
        q = q.filter(Payment.status == status)

    total = q.count()
    payments = q.order_by(Payment.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "payments": [_payment_dict(p, db) for p in payments],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get(
    "/payments/{payment_id}",
    summary="Get payment detail",
)
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _payment_dict(_get_payment(payment_id, db), db)


# =============================================================================
# DASHBOARD
# =============================================================================

@router.get(
    "/dashboard",
    summary="Revenue dashboard — MRR, ARR, revenue, pending payments (Super Admin only)",
)
def billing_dashboard(
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── MRR / ARR — sum of active/trialing subscriptions' monthly plan price ──
    mrr_rows = (
        db.query(SubscriptionPlan.price_monthly_usd)
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.status.in_(["active", "trialing"]))
        .all()
    )
    mrr = float(sum(row[0] for row in mrr_rows)) if mrr_rows else 0.0
    arr = mrr * 12

    # ── Revenue (from succeeded payments) ─────────────────────────────────
    total_revenue = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "succeeded")
        .scalar() or 0
    )
    revenue_this_month = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "succeeded", Payment.paid_at >= month_start)
        .scalar() or 0
    )

    # ── Pending payments — unpaid/partially-paid invoices ─────────────────
    pending_q = db.query(Invoice).filter(
        Invoice.status.in_(["sent", "partially_paid", "overdue"])
    )
    pending_amount = (
        db.query(func.coalesce(func.sum(Invoice.amount_due), 0))
        .filter(Invoice.status.in_(["sent", "partially_paid", "overdue"]))
        .scalar() or 0
    )
    pending_count = pending_q.count()

    # ── Overdue invoices ───────────────────────────────────────────────────
    overdue_q = db.query(Invoice).filter(
        Invoice.due_date < now,
        Invoice.status.in_(["sent", "partially_paid", "overdue"]),
    )
    overdue_amount = (
        db.query(func.coalesce(func.sum(Invoice.amount_due), 0))
        .filter(
            Invoice.due_date < now,
            Invoice.status.in_(["sent", "partially_paid", "overdue"]),
        )
        .scalar() or 0
    )
    overdue_count = overdue_q.count()

    # ── Invoice status breakdown ──────────────────────────────────────────
    status_rows = (
        db.query(Invoice.status, func.count(Invoice.id).label("count"))
        .group_by(Invoice.status)
        .all()
    )
    by_status = {row.status: row.count for row in status_rows}

    # ── Recent activity ───────────────────────────────────────────────────
    recent_invoices = db.query(Invoice).order_by(Invoice.issue_date.desc()).limit(5).all()
    recent_payments = db.query(Payment).order_by(Payment.created_at.desc()).limit(5).all()

    return {
        "mrr": round(mrr, 2),
        "arr": round(arr, 2),
        "revenue": {
            "total":      round(float(total_revenue), 2),
            "this_month": round(float(revenue_this_month), 2),
        },
        "pending_payments": {
            "count":  pending_count,
            "amount": round(float(pending_amount), 2),
        },
        "overdue_invoices": {
            "count":  overdue_count,
            "amount": round(float(overdue_amount), 2),
        },
        "invoices_by_status": {
            s: by_status.get(s, 0) for s in INVOICE_STATUSES
        },
        "recent_invoices": [_invoice_dict(inv, db) for inv in recent_invoices],
        "recent_payments": [_payment_dict(p, db) for p in recent_payments],
    }