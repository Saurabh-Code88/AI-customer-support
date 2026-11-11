"""SQLAlchemy models for orders, tickets, audit events, and idempotent refunds."""
from decimal import Decimal

from sqlalchemy import Float, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_email: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    days_since_delivery: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    refunded: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))


class RefundRecord(Base):
    __tablename__ = "refund_records"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), primary_key=True)
    result_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class TicketRecord(Base):
    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    values_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("ticket_id", "position", name="uq_audit_ticket_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)


class UsageCounter(Base):
    """Atomic fixed-window quota counter; keys stored here are hashed identifiers."""
    __tablename__ = "usage_counters"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
