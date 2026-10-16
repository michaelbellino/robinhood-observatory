"""Independent durable market quote checkpoint; account snapshots remain separate."""
from alembic import op
import sqlalchemy as sa

revision = "88d4ee07a312"
down_revision = "2ecda21af16c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("market_state")
