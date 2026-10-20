"""Research inbox and durable device notification delivery."""
from alembic import op
import sqlalchemy as sa
revision = 'c7210dea9101'
down_revision = 'b6217cdf403a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('trade_ideas',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('terms_hash', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(40), nullable=False),
        sa.Column('preview', sa.JSON()),
        sa.Column('preview_at', sa.DateTime(timezone=True)),
        sa.Column('preview_started_at', sa.DateTime(timezone=True)),
        sa.Column('broker_order_id', sa.String(36)))
    op.create_index('ix_trade_ideas_created_at', 'trade_ideas', ['created_at'])
    op.create_table('push_devices',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('owner', sa.String(255), nullable=False),
        sa.Column('subscription_cipher', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False))
    op.create_table('push_deliveries',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('event_id', sa.String(64), nullable=False),
        sa.Column('device_id', sa.String(64), nullable=False),
        sa.Column('state', sa.String(30), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_push_deliveries_event_id', 'push_deliveries', ['event_id'])


def downgrade():
    for table in ('push_deliveries', 'push_devices', 'trade_ideas'):
        op.drop_table(table)
