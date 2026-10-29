"""Store immutable owner-confirmed research mandate versions."""
from alembic import op
import sqlalchemy as sa

revision = "f1320dda1401"
down_revision = "f0310dda1301"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_mandates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("account_binding", sa.String(36), nullable=False),
        sa.Column("account_label", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("research_mandates")
