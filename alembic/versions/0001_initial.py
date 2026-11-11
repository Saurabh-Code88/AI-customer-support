"""Create orders, refunds, tickets, and audit tables."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("order_id", sa.String(length=32), primary_key=True),
        sa.Column("customer_email", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("days_since_delivery", sa.Integer(), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("refunded", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.create_table(
        "refund_records",
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=32), nullable=False),
        sa.Column("result_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.PrimaryKeyConstraint("ticket_id", "order_id"),
    )
    op.create_table(
        "tickets",
        sa.Column("ticket_id", sa.String(length=64), primary_key=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("resolved_at", sa.Float(), nullable=True),
        sa.Column("values_json", sa.JSON(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("ticket_id", "position", name="uq_audit_ticket_position"),
    )
    op.create_index("ix_audit_events_ticket_id", "audit_events", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_ticket_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("tickets")
    op.drop_table("refund_records")
    op.drop_table("orders")
