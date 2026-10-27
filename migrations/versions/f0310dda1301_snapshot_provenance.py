"""Account provenance and read-only manual order observations; historical evidence stays unknown."""
from alembic import op
import sqlalchemy as sa

revision = "f0310dda1301"
down_revision = "e9210dda1201"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("monitor_state", sa.Column("account_binding", sa.String(36)))
    op.add_column("snapshots", sa.Column("account_binding", sa.String(36)))
    op.add_column("snapshots", sa.Column("generation", sa.Integer()))
    op.add_column("snapshots", sa.Column("collection_started_at", sa.DateTime(timezone=True)))
    op.add_column("trade_ideas", sa.Column("account_binding", sa.String(36)))
    op.add_column("trade_ideas", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("trade_ideas", sa.Column("broker_observation", sa.JSON()))
    op.add_column("trade_ideas", sa.Column("broker_observed_at", sa.DateTime(timezone=True)))
    # No historical row is assigned an account or review timestamp retroactively.


def downgrade():
    for name in ("broker_observed_at", "broker_observation", "reviewed_at", "account_binding"):
        op.drop_column("trade_ideas", name)
    for name in ("collection_started_at", "generation", "account_binding"):
        op.drop_column("snapshots", name)
    op.drop_column("monitor_state", "account_binding")
