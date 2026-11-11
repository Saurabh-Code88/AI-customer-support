"""Seed or reset the five bundled demonstration orders."""
from sqlalchemy import delete, select

from .database import get_session_factory, run_migrations
from .models import Order, RefundRecord

DEMO_ORDERS = (
    {"order_id": "A1001", "customer_email": "a@example.com", "status": "delivered", "days_since_delivery": 5, "total": 1499.0, "address": "12 MG Road, Noida", "refunded": 0.0},
    {"order_id": "A1002", "customer_email": "b@example.com", "status": "delivered", "days_since_delivery": 3, "total": 5999.0, "address": "7 Sector 62, Noida", "refunded": 0.0},
    {"order_id": "A1003", "customer_email": "c@example.com", "status": "processing", "days_since_delivery": 0, "total": 799.0, "address": "44 Park Street, Kolkata", "refunded": 0.0},
    {"order_id": "A1004", "customer_email": "d@example.com", "status": "delivered", "days_since_delivery": 45, "total": 999.0, "address": "9 Civil Lines, Delhi", "refunded": 0.0},
    {"order_id": "A1005", "customer_email": "e@example.com", "status": "shipped", "days_since_delivery": 0, "total": 2499.0, "address": "3 Residency Rd, Bengaluru", "refunded": 0.0},
)


def seed_orders(*, reset: bool = False) -> None:
    """Insert missing demo orders, or restore the five demo rows and refunds on reset."""
    order_ids = [order["order_id"] for order in DEMO_ORDERS]
    factory = get_session_factory()
    with factory.begin() as session:
        if reset:
            session.execute(delete(RefundRecord).where(RefundRecord.order_id.in_(order_ids)))
            session.execute(delete(Order).where(Order.order_id.in_(order_ids)))
        existing = set(session.scalars(select(Order.order_id).where(Order.order_id.in_(order_ids))))
        session.add_all(Order(**order) for order in DEMO_ORDERS if order["order_id"] not in existing)


def main() -> None:
    run_migrations()
    seed_orders()
    print("Seeded 5 demo orders.")


if __name__ == "__main__":
    main()
