"""Database-backed tool implementations for orders and refunds."""
from decimal import Decimal
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .database import get_session_factory
from .models import Order, RefundRecord
from .seed import seed_orders


class ToolError(Exception):
    pass


def reset():
    """Restore the five demo orders and clear their idempotency ledger."""
    seed_orders(reset=True)


def _order_dict(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "customer_email": order.customer_email,
        "status": order.status,
        "days_since_delivery": order.days_since_delivery,
        "total": float(order.total),
        "address": order.address,
        "refunded": float(order.refunded),
    }


def get_order(order_id):
    with get_session_factory()() as session:
        order = session.get(Order, order_id)
        return _order_dict(order) if order else None


def list_orders() -> list[dict]:
    with get_session_factory()() as session:
        return [_order_dict(order) for order in session.scalars(select(Order).order_by(Order.order_id))]


def order_status(order_id, **_):
    order = get_order(order_id)
    if order is None:
        raise ToolError(f"Unknown order: {order_id}")
    return {"ok": True, "order_id": order_id, "status": order["status"]}


def _refund_result(record: RefundRecord) -> dict:
    return dict(record.result_json)


def issue_refund(order_id, amount=None, ticket_id=None, **_):
    if not ticket_id:
        raise ToolError("ticket_id is required for an idempotent refund")
    factory = get_session_factory()
    try:
        with factory.begin() as session:
            order = session.scalar(
                select(Order).where(Order.order_id == order_id).with_for_update()
            )
            if order is None:
                raise ToolError(f"Unknown order: {order_id}")
            existing = session.get(RefundRecord, (ticket_id, order_id))
            if existing:
                return _refund_result(existing)

            refund_amount = Decimal(str(amount)) if amount is not None else Decimal(order.total)
            order.refunded = Decimal(order.refunded) + refund_amount
            refunded = float(refund_amount)
            result = {
                "ok": True,
                "order_id": order_id,
                "refunded": refunded,
                "refunded_text": f"{refunded:,.2f}",
            }
            session.add(
                RefundRecord(
                    ticket_id=ticket_id,
                    order_id=order_id,
                    result_amount=refund_amount,
                    result_json=result,
                    created_at=time.time(),
                )
            )
        return result
    except IntegrityError:
        # A concurrent request may have committed this same idempotency key first.
        with factory() as session:
            existing = session.get(RefundRecord, (ticket_id, order_id))
            if existing:
                return _refund_result(existing)
        raise


def update_address(order_id, new_address=None, **_):
    if not new_address:
        raise ToolError("new_address is required")
    with get_session_factory().begin() as session:
        order = session.get(Order, order_id)
        if order is None:
            raise ToolError(f"Unknown order: {order_id}")
        order.address = new_address
    return {"ok": True, "order_id": order_id, "address": new_address}


TOOLS = {"order_status": order_status, "issue_refund": issue_refund, "update_address": update_address}


def run_tool(name, **kwargs):
    if name not in TOOLS:
        raise ToolError(f"Unknown tool: {name}")
    return TOOLS[name](**kwargs)
