"""Stub backend. Replace with real API calls (helpdesk, orders DB, payments)."""
import copy
from threading import Lock

_ORDERS = {
    "A1001": {"customer_email": "a@example.com", "status": "delivered", "days_since_delivery": 5, "total": 1499.0, "address": "12 MG Road, Noida", "refunded": 0.0},
    "A1002": {"customer_email": "b@example.com", "status": "delivered", "days_since_delivery": 3, "total": 5999.0, "address": "7 Sector 62, Noida", "refunded": 0.0},
    "A1003": {"customer_email": "c@example.com", "status": "processing", "days_since_delivery": 0, "total": 799.0, "address": "44 Park Street, Kolkata", "refunded": 0.0},
    "A1004": {"customer_email": "d@example.com", "status": "delivered", "days_since_delivery": 45, "total": 999.0, "address": "9 Civil Lines, Delhi", "refunded": 0.0},
    "A1005": {"customer_email": "e@example.com", "status": "shipped", "days_since_delivery": 0, "total": 2499.0, "address": "3 Residency Rd, Bengaluru", "refunded": 0.0},
}
ORDERS = copy.deepcopy(_ORDERS)
_REFUND_RESULTS = {}
_REFUND_LOCK = Lock()


def reset():
    ORDERS.clear()
    ORDERS.update(copy.deepcopy(_ORDERS))
    with _REFUND_LOCK:
        _REFUND_RESULTS.clear()


class ToolError(Exception):
    pass


def get_order(order_id):
    o = ORDERS.get(order_id)
    return dict(o, order_id=order_id) if o else None


def order_status(order_id, **_):
    return {"ok": True, "order_id": order_id, "status": ORDERS[order_id]["status"]}


def issue_refund(order_id, amount=None, ticket_id=None, **_):
    if not ticket_id:
        raise ToolError("ticket_id is required for an idempotent refund")
    key = (ticket_id, order_id)
    with _REFUND_LOCK:
        if key in _REFUND_RESULTS:
            return dict(_REFUND_RESULTS[key])
        o = ORDERS[order_id]
        amt = amount if amount else o["total"]
        o["refunded"] += amt
        result = {"ok": True, "order_id": order_id, "refunded": amt, "refunded_text": f"{amt:,.2f}"}
        _REFUND_RESULTS[key] = result
        return dict(result)


def update_address(order_id, new_address=None, **_):
    if not new_address:
        raise ToolError("new_address is required")
    ORDERS[order_id]["address"] = new_address
    return {"ok": True, "order_id": order_id, "address": new_address}


TOOLS = {"order_status": order_status, "issue_refund": issue_refund, "update_address": update_address}


def run_tool(name, **kwargs):
    if name not in TOOLS:
        raise ToolError(f"Unknown tool: {name}")
    return TOOLS[name](**kwargs)
