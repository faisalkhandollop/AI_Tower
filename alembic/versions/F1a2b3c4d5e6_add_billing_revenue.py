"""
Billing & Revenue — Phase 2
=============================
Revision: f1a2b3c4d5e6
Creates:
  - invoices
  - payments

Does NOT alter any existing table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = 'f1a2b3c4d5e6'
down_revision = 'd1e2f3a4b5c6'   # add_subscription_management migration
branch_labels = None
depends_on    = None


def upgrade():
    # ── ENUM types ────────────────────────────────────────────────────────────
    invoice_status_enum = postgresql.ENUM(
        'draft', 'sent', 'paid', 'partially_paid', 'overdue', 'void', 'refunded',
        name='invoice_status_enum', create_type=True
    )
    invoice_status_enum.create(op.get_bind(), checkfirst=True)

    payment_status_enum = postgresql.ENUM(
        'pending', 'succeeded', 'failed', 'refunded',
        name='payment_status_enum', create_type=True
    )
    payment_status_enum.create(op.get_bind(), checkfirst=True)

    payment_method_enum = postgresql.ENUM(
        'card', 'bank_transfer', 'upi', 'paypal', 'manual', 'other',
        name='payment_method_enum', create_type=True
    )
    payment_method_enum.create(op.get_bind(), checkfirst=True)

    # ── invoices ──────────────────────────────────────────────────────────────
    op.create_table(
        'invoices',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('subscription_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('subscriptions.id'), nullable=True),
        sa.Column('invoice_number',  sa.String(50), nullable=False, unique=True),
        sa.Column('status',          sa.Enum('draft', 'sent', 'paid', 'partially_paid', 'overdue', 'void', 'refunded', name='invoice_status_enum'), nullable=False, server_default='draft'),
        sa.Column('currency',        sa.String(3),  nullable=False, server_default='USD'),
        sa.Column('line_items',      postgresql.JSON, nullable=False, server_default='[]'),
        sa.Column('subtotal',        sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('tax',             sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('total',           sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('amount_paid',     sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('amount_due',      sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('period_start',    sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end',      sa.DateTime(timezone=True), nullable=True),
        sa.Column('issue_date',      sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('due_date',        sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_at',       sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes',           sa.Text, nullable=True),
        sa.Column('created_by',      postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_invoices_org_id',          'invoices', ['organization_id'])
    op.create_index('ix_invoices_subscription_id', 'invoices', ['subscription_id'])
    op.create_index('ix_invoices_status',          'invoices', ['status'])
    op.create_index('ix_invoices_due_date',        'invoices', ['due_date'])
    op.create_index('ix_invoices_issue_date',      'invoices', ['issue_date'])

    # ── payments ──────────────────────────────────────────────────────────────
    op.create_table(
        'payments',
        sa.Column('id',                    postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id',       postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('invoice_id',            postgresql.UUID(as_uuid=True), sa.ForeignKey('invoices.id'),      nullable=True),
        sa.Column('amount',                sa.Numeric(12, 2), nullable=False),
        sa.Column('currency',              sa.String(3), nullable=False, server_default='USD'),
        sa.Column('payment_method',        sa.Enum('card', 'bank_transfer', 'upi', 'paypal', 'manual', 'other', name='payment_method_enum'), nullable=False, server_default='manual'),
        sa.Column('status',                sa.Enum('pending', 'succeeded', 'failed', 'refunded', name='payment_status_enum'), nullable=False, server_default='succeeded'),
        sa.Column('transaction_reference', sa.String(200), nullable=True),
        sa.Column('notes',                 sa.Text, nullable=True),
        sa.Column('paid_at',               sa.DateTime(timezone=True), nullable=True),
        sa.Column('recorded_by',           postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_payments_org_id',     'payments', ['organization_id'])
    op.create_index('ix_payments_invoice_id', 'payments', ['invoice_id'])
    op.create_index('ix_payments_status',     'payments', ['status'])
    op.create_index('ix_payments_paid_at',    'payments', ['paid_at'])


def downgrade():
    op.drop_index('ix_payments_paid_at',    'payments')
    op.drop_index('ix_payments_status',     'payments')
    op.drop_index('ix_payments_invoice_id', 'payments')
    op.drop_index('ix_payments_org_id',     'payments')
    op.drop_table('payments')

    op.drop_index('ix_invoices_issue_date',      'invoices')
    op.drop_index('ix_invoices_due_date',        'invoices')
    op.drop_index('ix_invoices_status',          'invoices')
    op.drop_index('ix_invoices_subscription_id', 'invoices')
    op.drop_index('ix_invoices_org_id',          'invoices')
    op.drop_table('invoices')

    op.execute("DROP TYPE IF EXISTS payment_method_enum")
    op.execute("DROP TYPE IF EXISTS payment_status_enum")
    op.execute("DROP TYPE IF EXISTS invoice_status_enum")