"""shared_request_budget

Revision ID: 2ecda21af16c
"""

from alembic import op
import sqlalchemy as sa

revision = "2ecda21af16c"
down_revision = "2b12aade0799"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "broker_request_budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_allowed", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text("INSERT INTO broker_request_budget (id) VALUES (1)"))


def downgrade():
    op.drop_table("broker_request_budget")
