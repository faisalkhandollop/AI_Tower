"""
app/db/models_billing.py
=========================
Phase 2 — Billing & Revenue Models — added on top of existing models.
Does NOT modify any existing table.

New tables:
  invoices   — billing invoices issued to organizations
  payments   — payments received against invoices (or standalone)
"""

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Index, Integer, Numeric, JSON, String, Text,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────
INVOICE_STATUSES = (
    "draft",
    "sent",
    "paid",
    "partially_paid",
    "overdue",
    "void",
    "refunded",
)

PAYMENT_STATUSES = (
    "pending",
    "succeeded",
    "failed",
    "refunded",
)

PAYMENT_METHODS = (
    "card",
    "bank_transfer",
    "upi",
    "paypal",
    "manual",
    "other",
)


# ─────────────────────────────────────────────────────────────
# INVOICES
# One invoice per billing cycle (or ad-hoc) for an organization.
# line_items: [{ "description": str, "quantity": number,
#                 "unit_price": number, "amount": number }]
# ─────────────────────────────────────────────────────────────
class Invoice(Base):
    __tablename__ = "invoices"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"),  nullable=False)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"),  nullable=True)

    invoice_number  = Column(String(50), nullable=False, unique=True)

    status          = Column(
        SAEnum(*INVOICE_STATUSES, name="invoice_status_enum"),
        nullable=False,
        default="draft",
    )

    currency        = Column(String(3), nullable=False, default="USD")
    line_items      = Column(JSON, nullable=False, default=list)

    subtotal        = Column(Numeric(12, 2), nullable=False, default=0)
    tax             = Column(Numeric(12, 2), nullable=False, default=0)
    total           = Column(Numeric(12, 2), nullable=False, default=0)
    amount_paid     = Column(Numeric(12, 2), nullable=False, default=0)
    amount_due      = Column(Numeric(12, 2), nullable=False, default=0)

    # Billing period this invoice covers
    period_start    = Column(DateTime(timezone=True), nullable=True)
    period_end      = Column(DateTime(timezone=True), nullable=True)

    issue_date      = Column(DateTime(timezone=True), server_default=func.now())
    due_date        = Column(DateTime(timezone=True), nullable=True)
    paid_at         = Column(DateTime(timezone=True), nullable=True)
    voided_at       = Column(DateTime(timezone=True), nullable=True)

    notes           = Column(Text, nullable=True)
    created_by      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_invoices_org_id",         "organization_id"),
        Index("ix_invoices_subscription_id","subscription_id"),
        Index("ix_invoices_status",         "status"),
        Index("ix_invoices_due_date",       "due_date"),
        Index("ix_invoices_issue_date",     "issue_date"),
    )


# ─────────────────────────────────────────────────────────────
# PAYMENTS
# A payment recorded against an invoice (or standalone, e.g. a
# manual top-up / one-off charge with invoice_id = NULL).
# ─────────────────────────────────────────────────────────────
class Payment(Base):
    __tablename__ = "payments"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    invoice_id      = Column(UUID(as_uuid=True), ForeignKey("invoices.id"),      nullable=True)

    amount          = Column(Numeric(12, 2), nullable=False)
    currency        = Column(String(3), nullable=False, default="USD")

    payment_method  = Column(
        SAEnum(*PAYMENT_METHODS, name="payment_method_enum"),
        nullable=False,
        default="manual",
    )

    status          = Column(
        SAEnum(*PAYMENT_STATUSES, name="payment_status_enum"),
        nullable=False,
        default="succeeded",
    )

    transaction_reference = Column(String(200), nullable=True)  # gateway/bank ref no.
    notes                  = Column(Text, nullable=True)

    paid_at         = Column(DateTime(timezone=True), nullable=True)
    recorded_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_payments_org_id",     "organization_id"),
        Index("ix_payments_invoice_id", "invoice_id"),
        Index("ix_payments_status",     "status"),
        Index("ix_payments_paid_at",    "paid_at"),
    )