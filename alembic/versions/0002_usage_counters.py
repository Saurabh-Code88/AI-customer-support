"""Add persistent counters for sandbox quotas and abuse protection."""
from alembic import op
import sqlalchemy as sa

revision = "0002_usage_counters"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "bucket_key", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
