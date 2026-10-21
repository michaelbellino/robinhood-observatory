"""Bind proposals to accounts and retain immutable approval evidence."""
from alembic import op
import sqlalchemy as sa

revision = "e9210dda1201"
down_revision = "d820aa129101"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trade_ideas", sa.Column("account_cipher", sa.Text()))
    op.add_column("trade_ideas", sa.Column("account_label", sa.String(80)))
    op.add_column("trade_ideas", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("trade_ideas", sa.Column("parent_id", sa.String(36)))
    op.create_index("uq_trade_idea_parent", "trade_ideas", ["parent_id"], unique=True)
    op.add_column("order_submissions", sa.Column("approval", sa.JSON()))
    op.add_column("order_submissions", sa.Column("approved_at", sa.DateTime(timezone=True)))
    # Old proposals have no provable creation-time account binding. Never infer it.
    op.execute("UPDATE trade_ideas SET status='needs_review' WHERE status='awaiting_review'")


def downgrade():
    op.drop_column("order_submissions", "approved_at")
    op.drop_column("order_submissions", "approval")
    op.drop_index("uq_trade_idea_parent", table_name="trade_ideas")
    for name in ("parent_id", "version", "account_label", "account_cipher"):
        op.drop_column("trade_ideas", name)
