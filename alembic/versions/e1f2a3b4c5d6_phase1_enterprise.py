"""
Phase 1 Enterprise Upgrade Migration
=====================================
Revision: e1f2a3b4c5d6
Creates:
  - roles
  - permissions
  - role_permissions
  - user_org_roles
  - conversations
  - messages
  - attachments
  - audit_logs
  - privileged_access_logs

Does NOT alter any existing table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision    = 'e1f2a3b4c5d6'
down_revision = 'c1d2e3f4a5b6'   # last existing migration in v4
branch_labels = None
depends_on    = None


def upgrade():
    # ── ENUM types ────────────────────────────────────────────────────────────
    role_name_enum = postgresql.ENUM(
        'SUPER_ADMIN', 'ORG_ADMIN', 'DEPARTMENT_MANAGER', 'TEAM_LEAD', 'USER',
        name='role_name_enum', create_type=True
    )
    role_name_enum.create(op.get_bind(), checkfirst=True)

    permission_name_enum = postgresql.ENUM(
        'view_users', 'create_users', 'update_users', 'delete_users',
        'view_costs', 'view_usage', 'export_reports',
        'manage_api_keys', 'view_api_keys',
        'view_conversations', 'delete_conversations',
        'manage_departments', 'manage_teams', 'manage_org_settings',
        'manage_organizations', 'view_platform_metrics',
        'manage_providers', 'manage_routing',
        name='permission_name_enum', create_type=True
    )
    permission_name_enum.create(op.get_bind(), checkfirst=True)

    audit_action_enum = postgresql.ENUM(
        'USER_CREATED', 'USER_UPDATED', 'USER_DEACTIVATED', 'USER_DELETED',
        'ROLE_ASSIGNED', 'ROLE_REVOKED', 'ROLE_CHANGED',
        'LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT', 'PASSWORD_CHANGED',
        'ORG_CREATED', 'ORG_UPDATED', 'ORG_SUSPENDED', 'ORG_DELETED',
        'TEAM_CREATED', 'TEAM_UPDATED', 'TEAM_DELETED',
        'DEPARTMENT_CREATED', 'DEPARTMENT_UPDATED', 'DEPARTMENT_DELETED',
        'MEMBER_ADDED', 'MEMBER_REMOVED',
        'API_KEY_ADDED', 'API_KEY_ROTATED', 'API_KEY_DELETED',
        'FILE_UPLOADED', 'FILE_DELETED',
        'CONVERSATION_DELETED', 'QUOTA_CHANGED', 'BUDGET_CHANGED',
        'REPORT_GENERATED', 'ALERT_TRIGGERED',
        name='audit_action_enum', create_type=True
    )
    audit_action_enum.create(op.get_bind(), checkfirst=True)

    # ── roles ─────────────────────────────────────────────────────────────────
    op.create_table(
        'roles',
        sa.Column('id',          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name',        sa.Enum('SUPER_ADMIN', 'ORG_ADMIN', 'DEPARTMENT_MANAGER', 'TEAM_LEAD', 'USER', name='role_name_enum'), unique=True, nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at',  sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── permissions ───────────────────────────────────────────────────────────
    op.create_table(
        'permissions',
        sa.Column('id',          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name',        sa.Text, unique=True, nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at',  sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── role_permissions ──────────────────────────────────────────────────────
    op.create_table(
        'role_permissions',
        sa.Column('id',            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('role_id',       postgresql.UUID(as_uuid=True), sa.ForeignKey('roles.id'), nullable=False),
        sa.Column('permission_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('permissions.id'), nullable=False),
        sa.UniqueConstraint('role_id', 'permission_id', name='uq_role_permission'),
    )
    op.create_index('ix_role_permissions_role_id', 'role_permissions', ['role_id'])

    # ── user_org_roles ────────────────────────────────────────────────────────
    op.create_table(
        'user_org_roles',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id',         postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=False),
        sa.Column('role_id',         postgresql.UUID(as_uuid=True), sa.ForeignKey('roles.id'),         nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('assigned_by',     postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=True),
        sa.Column('assigned_at',     sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'role_id', 'organization_id', name='uq_user_org_role'),
    )
    op.create_index('ix_user_org_roles_user_id', 'user_org_roles', ['user_id'])
    op.create_index('ix_user_org_roles_org_id',  'user_org_roles', ['organization_id'])

    # ── conversations ─────────────────────────────────────────────────────────
    op.create_table(
        'conversations',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('user_id',         postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=False),
        sa.Column('session_id',      sa.String(100), sa.ForeignKey('sessions.session_id'), nullable=True),
        sa.Column('title',           sa.String(500), nullable=True),
        sa.Column('department',      sa.String(100), nullable=True),
        sa.Column('team_id',         postgresql.UUID(as_uuid=True), sa.ForeignKey('teams.id'), nullable=True),
        sa.Column('is_archived',     sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_conversations_org_id',     'conversations', ['organization_id'])
    op.create_index('ix_conversations_user_id',    'conversations', ['user_id'])
    op.create_index('ix_conversations_created_at', 'conversations', ['created_at'])

    # ── messages ──────────────────────────────────────────────────────────────
    op.create_table(
        'messages',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('role',            sa.String(20),  nullable=False),
        sa.Column('content',         sa.Text,        nullable=False),
        sa.Column('token_count',     sa.Integer,     nullable=True),
        sa.Column('provider',        sa.String(100), nullable=True),
        sa.Column('model',           sa.String(100), nullable=True),
        sa.Column('cost',            sa.Numeric(10, 6), nullable=True),
        sa.Column('created_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('ix_messages_org_id',          'messages', ['organization_id'])
    op.create_index('ix_messages_created_at',      'messages', ['created_at'])

    # ── attachments ───────────────────────────────────────────────────────────
    op.create_table(
        'attachments',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('message_id',      postgresql.UUID(as_uuid=True), sa.ForeignKey('messages.id'),      nullable=False),
        sa.Column('uploaded_by',     postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=False),
        sa.Column('filename',        sa.String(500), nullable=False),
        sa.Column('storage_path',    sa.Text,        nullable=False),
        sa.Column('mime_type',       sa.String(200), nullable=True),
        sa.Column('file_size',       sa.Integer,     nullable=True),
        sa.Column('created_at',      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_attachments_org_id',     'attachments', ['organization_id'])
    op.create_index('ix_attachments_message_id', 'attachments', ['message_id'])

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        'audit_logs',
        sa.Column('id',              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('actor_id',        postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=True),
        sa.Column('actor_email',     sa.String(255), nullable=True),
        sa.Column('actor_role',      sa.String(50),  nullable=True),
        sa.Column('action',          sa.Text,        nullable=False),
        sa.Column('resource_type',   sa.String(100), nullable=True),
        sa.Column('resource_id',     sa.String(100), nullable=True),
        sa.Column('resource_name',   sa.String(500), nullable=True),
        sa.Column('detail',          sa.Text,        nullable=True),
        sa.Column('ip_address',      sa.String(45),  nullable=True),
        sa.Column('user_agent',      sa.String(500), nullable=True),
        sa.Column('timestamp',       sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_audit_logs_org_id',    'audit_logs', ['organization_id'])
    op.create_index('ix_audit_logs_actor_id',  'audit_logs', ['actor_id'])
    op.create_index('ix_audit_logs_action',    'audit_logs', ['action'])
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])

    # ── privileged_access_logs ────────────────────────────────────────────────
    op.create_table(
        'privileged_access_logs',
        sa.Column('id',                postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('employee_id',       postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'),         nullable=True),
        sa.Column('employee_name',     sa.String(200), nullable=False),
        sa.Column('employee_email',    sa.String(255), nullable=False),
        sa.Column('role',              sa.String(100), nullable=False),
        sa.Column('organization_id',   postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('organization_name', sa.String(200), nullable=True),
        sa.Column('resource_accessed', sa.String(200), nullable=False),
        sa.Column('reason',            sa.Text,        nullable=False),
        sa.Column('ip_address',        sa.String(45),  nullable=True),
        sa.Column('access_granted',    sa.Boolean,     nullable=False, server_default='true'),
        sa.Column('timestamp',         sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_priv_access_employee_id', 'privileged_access_logs', ['employee_id'])
    op.create_index('ix_priv_access_org_id',      'privileged_access_logs', ['organization_id'])
    op.create_index('ix_priv_access_timestamp',   'privileged_access_logs', ['timestamp'])


def downgrade():
    op.drop_table('privileged_access_logs')
    op.drop_table('audit_logs')
    op.drop_table('attachments')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('user_org_roles')
    op.drop_table('role_permissions')
    op.drop_table('permissions')
    op.drop_table('roles')

    op.execute("DROP TYPE IF EXISTS audit_action_enum")
    op.execute("DROP TYPE IF EXISTS permission_name_enum")
    op.execute("DROP TYPE IF EXISTS role_name_enum")
