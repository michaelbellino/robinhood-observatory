"""Retain bounded quote-quality evidence without prices or account values."""
from alembic import op
import sqlalchemy as sa

revision = "a940320a7611"
down_revision = "88d4ee07a312"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("market_samples",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("payload", sa.JSON(), nullable=False))
    op.create_index("ix_market_samples_observed_at", "market_samples", ["observed_at"])


def downgrade():
    op.drop_table("market_samples")
