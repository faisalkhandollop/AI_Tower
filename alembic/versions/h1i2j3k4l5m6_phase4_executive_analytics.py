"""phase4_executive_analytics

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-06-12 00:00:00.000000

Phase 4 — Executive Analytics
New tables:
  company_usage_summary
  analytics_snapshots
  dashboard_widgets
  model_benchmarks
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'h1i2j3k4l5m6'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ENUMS ──────────────────────────────────────────────────────────────────
    snapshot_type_enum = postgresql.ENUM(
        'daily', 'weekly', 'monthly', 'quarterly',
        name='snapshot_type_enum',
        create_type=True,
    )
    snapshot_type_enum.create(op.get_bind(), checkfirst=True)

    # ── company_usage_summary ──────────────────────────────────────────────────
    op.create_table(
        'company_usage_summary',
        sa.Column('id',               postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('date',             sa.String(10),  nullable=False),
        sa.Column('organization_id',  postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('organization_name',sa.String(200), nullable=True),
        sa.Column('provider',         sa.String(100), nullable=True),
        sa.Column('model',            sa.String(200), nullable=True),
        sa.Column('total_requests',   sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('total_tokens',     sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('input_tokens',     sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('output_tokens',    sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('total_cost_usd',   sa.Numeric(14, 6), nullable=False, server_default='0'),
        sa.Column('active_users',     sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('avg_latency_ms',   sa.Float(),     nullable=True),
        sa.Column('error_count',      sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('computed_at',      sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('date', 'organization_id', 'provider', 'model',
                            name='uq_company_usage_summary'),
    )
    op.create_index('ix_cus_date',    'company_usage_summary', ['date'])
    op.create_index('ix_cus_org_id',  'company_usage_summary', ['organization_id'])
    op.create_index('ix_cus_provider','company_usage_summary', ['provider'])
    op.create_index('ix_cus_model',   'company_usage_summary', ['model'])

    # ── analytics_snapshots ────────────────────────────────────────────────────
    op.create_table(
        'analytics_snapshots',
        sa.Column('id',            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('snapshot_type', snapshot_type_enum, nullable=False),
        sa.Column('period_label',  sa.String(20), nullable=False),
        sa.Column('scope',         sa.String(50), nullable=False, server_default='platform'),
        sa.Column('data',          postgresql.JSON(), nullable=False, server_default='{}'),
        sa.Column('generated_at',  sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('snapshot_type', 'period_label', 'scope',
                            name='uq_analytics_snapshot'),
    )
    op.create_index('ix_as_type_period', 'analytics_snapshots', ['snapshot_type', 'period_label'])
    op.create_index('ix_as_scope',       'analytics_snapshots', ['scope'])

    # ── dashboard_widgets ──────────────────────────────────────────────────────
    op.create_table(
        'dashboard_widgets',
        sa.Column('id',         postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('admin_id',   postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('widget_key', sa.String(100), nullable=False),
        sa.Column('position',   sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('is_visible', sa.Boolean(),   nullable=False, server_default='true'),
        sa.Column('config',     postgresql.JSON(), nullable=False, server_default='{}'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('admin_id', 'widget_key', name='uq_dashboard_widget'),
    )
    op.create_index('ix_dw_admin_id', 'dashboard_widgets', ['admin_id'])

    # ── model_benchmarks ───────────────────────────────────────────────────────
    op.create_table(
        'model_benchmarks',
        sa.Column('id',                  postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('provider',            sa.String(100), nullable=False),
        sa.Column('model',               sa.String(200), nullable=False),
        sa.Column('sampled_at',          sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('sample_window',       sa.String(10),  nullable=False, server_default='1h'),
        sa.Column('avg_latency_ms',      sa.Float(),     nullable=True),
        sa.Column('p50_latency_ms',      sa.Float(),     nullable=True),
        sa.Column('p95_latency_ms',      sa.Float(),     nullable=True),
        sa.Column('p99_latency_ms',      sa.Float(),     nullable=True),
        sa.Column('avg_cost_per_req',    sa.Numeric(10, 8), nullable=True),
        sa.Column('avg_input_cost',      sa.Numeric(10, 8), nullable=True),
        sa.Column('avg_output_cost',     sa.Numeric(10, 8), nullable=True),
        sa.Column('total_requests',      sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('error_count',         sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('error_rate',          sa.Float(),     nullable=True),
        sa.Column('avg_tokens_in',       sa.Float(),     nullable=True),
        sa.Column('avg_tokens_out',      sa.Float(),     nullable=True),
        sa.Column('routing_selections',  sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('fallback_count',      sa.Integer(),   nullable=False, server_default='0'),
    )
    op.create_index('ix_mb_provider_model', 'model_benchmarks', ['provider', 'model'])
    op.create_index('ix_mb_sampled_at',     'model_benchmarks', ['sampled_at'])
    op.create_index('ix_mb_sample_window',  'model_benchmarks', ['sample_window'])


def downgrade() -> None:
    op.drop_table('model_benchmarks')
    op.drop_table('dashboard_widgets')
    op.drop_table('analytics_snapshots')
    op.drop_table('company_usage_summary')

    op.execute("DROP TYPE IF EXISTS snapshot_type_enum")
