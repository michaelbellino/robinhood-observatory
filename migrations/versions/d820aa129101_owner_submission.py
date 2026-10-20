"""Durable results for direct owner-operated order submission."""
from alembic import op
import sqlalchemy as sa

revision = "d820aa129101"
down_revision = "c7210dea9101"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trade_ideas", sa.Column("preview_generation", sa.Integer()))
    op.create_table("order_submissions",
        sa.Column("idea_id", sa.String(36), primary_key=True),
        sa.Column("ref_id", sa.String(36), nullable=False, unique=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order", sa.JSON()),
        sa.Column("message", sa.String(400)))


def downgrade():
    op.drop_table("order_submissions")
    op.drop_column("trade_ideas", "preview_generation")
