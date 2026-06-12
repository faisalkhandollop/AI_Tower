"""
Subscription Management — Phase 1 SaaS
========================================
Revision: d1e2f3a4b5c6
Creates:
  - subscription_plans
  - subscriptions

Seeds 3 default plans: Free, Pro, Enterprise.
Does NOT alter any existing table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid
from datetime import datetime, timezone

revision      = 'd1e2f3a4b5c6'
down_revision = 'e1f2a3b4c5d6'   # phase1_enterprise migration
branch_labels = None
depends_on    = None


def upgrade():
    # ── ENUM types ────────────────────────────────────────────────────────────
    plan_tier_enum = postgresql.ENUM(
        'free', 'pro', 'enterprise',
        name='plan_tier_enum', create_type=True
    )
    plan_tier_enum.create(op.get_bind(), checkfirst=True)

    subscription_status_enum = postgresql.ENUM(
        'active', 'trialing', 'past_due', 'suspended', 'cancelled', 'expired',
        name='subscription_status_enum', create_type=True
    )
    subscription_status_enum.create(op.get_bind(), checkfirst=True)

    # ── subscription_plans ────────────────────────────────────────────────────
    op.create_table(
        'subscription_plans',
        sa.Column('id',                    postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name',                  sa.String(100),  nullable=False, unique=True),
        sa.Column('tier',                  sa.Enum('free', 'pro', 'enterprise', name='plan_tier_enum'), nullable=False),
        sa.Column('description',           sa.Text,         nullable=True),
        sa.Column('price_monthly_usd',     sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('price_yearly_usd',      sa.Numeric(10, 2), nullable=True),
        sa.Column('max_users',             sa.Integer,      nullable=True),
        sa.Column('max_teams',             sa.Integer,      nullable=True),
        sa.Column('max_departments',       sa.Integer,      nullable=True),
        sa.Column('monthly_token_quota',   sa.Integer,      nullable=True),
        sa.Column('monthly_request_quota', sa.Integer,      nullable=True),
        sa.Column('max_api_keys',          sa.Integer,      nullable=True),
        sa.Column('allowed_providers',     postgresql.JSON, nullable=False, server_default='[]'),
        sa.Column('features',              postgresql.JSON, nullable=False, server_default='{}'),
        sa.Column('is_active',             sa.Boolean,      nullable=False, server_default='true'),
        sa.Column('is_public',             sa.Boolean,      nullable=False, server_default='true'),
        sa.Column('created_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_subscription_plans_tier',      'subscription_plans', ['tier'])
    op.create_index('ix_subscription_plans_is_active', 'subscription_plans', ['is_active'])

    # ── subscriptions ─────────────────────────────────────────────────────────
    op.create_table(
        'subscriptions',
        sa.Column('id',                    postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id',       postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False, unique=True),
        sa.Column('plan_id',               postgresql.UUID(as_uuid=True), sa.ForeignKey('subscription_plans.id'), nullable=False),
        sa.Column('status',                sa.Enum('active', 'trialing', 'past_due', 'suspended', 'cancelled', 'expired', name='subscription_status_enum'), nullable=False, server_default='active'),
        sa.Column('current_period_start',  sa.DateTime(timezone=True), nullable=False),
        sa.Column('current_period_end',    sa.DateTime(timezone=True), nullable=False),
        sa.Column('token_quota_override',  sa.Integer, nullable=True),
        sa.Column('request_quota_override',sa.Integer, nullable=True),
        sa.Column('user_limit_override',   sa.Integer, nullable=True),
        sa.Column('trial_ends_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at',          sa.DateTime(timezone=True), nullable=True),
        sa.Column('suspended_at',          sa.DateTime(timezone=True), nullable=True),
        sa.Column('suspension_reason',     sa.Text,    nullable=True),
        sa.Column('assigned_by',           postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('notes',                 sa.Text,    nullable=True),
        sa.Column('created_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',            sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_subscriptions_org_id',     'subscriptions', ['organization_id'])
    op.create_index('ix_subscriptions_plan_id',    'subscriptions', ['plan_id'])
    op.create_index('ix_subscriptions_status',     'subscriptions', ['status'])
    op.create_index('ix_subscriptions_period_end', 'subscriptions', ['current_period_end'])

    # ── Seed default plans ────────────────────────────────────────────────────
    import json
    plans_table = sa.table(
        'subscription_plans',
        sa.column('id'),
        sa.column('name'),
        sa.column('tier'),
        sa.column('description'),
        sa.column('price_monthly_usd'),
        sa.column('price_yearly_usd'),
        sa.column('max_users'),
        sa.column('max_teams'),
        sa.column('max_departments'),
        sa.column('monthly_token_quota'),
        sa.column('monthly_request_quota'),
        sa.column('max_api_keys'),
        sa.column('allowed_providers'),
        sa.column('features'),
        sa.column('is_active'),
        sa.column('is_public'),
    )

    op.bulk_insert(plans_table, [
        {
            'id':                    str(uuid.uuid4()),
            'name':                  'Free',
            'tier':                  'free',
            'description':           'Get started with essential AI features. Perfect for individuals and small trials.',
            'price_monthly_usd':     0.00,
            'price_yearly_usd':      0.00,
            'max_users':             5,
            'max_teams':             1,
            'max_departments':       1,
            'monthly_token_quota':   100_000,
            'monthly_request_quota': 500,
            'max_api_keys':          1,
            'allowed_providers':     json.dumps(['groq']),
            'features':              json.dumps({'kb': False, 'audit': False, 'reports': False, 'custom_routing': False, 'sso': False}),
            'is_active':             True,
            'is_public':             True,
        },
        {
            'id':                    str(uuid.uuid4()),
            'name':                  'Pro',
            'tier':                  'pro',
            'description':           'Full platform access for growing teams. Includes audit logs, reports, and multi-provider routing.',
            'price_monthly_usd':     99.00,
            'price_yearly_usd':      990.00,
            'max_users':             50,
            'max_teams':             10,
            'max_departments':       5,
            'monthly_token_quota':   5_000_000,
            'monthly_request_quota': 25_000,
            'max_api_keys':          10,
            'allowed_providers':     json.dumps(['groq', 'openai', 'claude', 'gemini', 'openrouter']),
            'features':              json.dumps({'kb': True, 'audit': True, 'reports': True, 'custom_routing': True, 'sso': False}),
            'is_active':             True,
            'is_public':             True,
        },
        {
            'id':                    str(uuid.uuid4()),
            'name':                  'Enterprise',
            'tier':                  'enterprise',
            'description':           'Unlimited scale, SSO, dedicated support, custom SLAs. Contact sales for pricing.',
            'price_monthly_usd':     499.00,
            'price_yearly_usd':      4990.00,
            'max_users':             None,
            'max_teams':             None,
            'max_departments':       None,
            'monthly_token_quota':   None,
            'monthly_request_quota': None,
            'max_api_keys':          None,
            'allowed_providers':     json.dumps(['groq', 'openai', 'claude', 'gemini', 'openrouter']),
            'features':              json.dumps({'kb': True, 'audit': True, 'reports': True, 'custom_routing': True, 'sso': True}),
            'is_active':             True,
            'is_public':             True,
        },
    ])


def downgrade():
    op.drop_index('ix_subscriptions_period_end', 'subscriptions')
    op.drop_index('ix_subscriptions_status',     'subscriptions')
    op.drop_index('ix_subscriptions_plan_id',    'subscriptions')
    op.drop_index('ix_subscriptions_org_id',     'subscriptions')
    op.drop_table('subscriptions')

    op.drop_index('ix_subscription_plans_is_active', 'subscription_plans')
    op.drop_index('ix_subscription_plans_tier',      'subscription_plans')
    op.drop_table('subscription_plans')

    op.execute("DROP TYPE IF EXISTS subscription_status_enum")
    op.execute("DROP TYPE IF EXISTS plan_tier_enum")