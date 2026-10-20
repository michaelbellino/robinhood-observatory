"""Add isolated paper-review rehearsals; no brokerage write capability."""
from alembic import op
import sqlalchemy as sa

revision = "b6217cdf403a"
down_revision = "a940320a7611"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_rehearsals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("terms_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
    )
    op.create_index("ix_review_rehearsals_created_at", "review_rehearsals", ["created_at"])


def downgrade():
    op.drop_table("review_rehearsals")
