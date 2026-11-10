"""Deterministic guardrails. The LLM proposes; this code disposes."""
from .config import REFUND_AUTO_LIMIT, REFUND_WINDOW_DAYS


def _r(allowed, reason, needs_approval=False):
    return {"allowed": allowed, "needs_approval": needs_approval, "reason": reason}


def check(decision: dict, order: dict | None, customer_email: str) -> dict:
    tool = decision.get("tool_name")
    if tool not in ("order_status", "issue_refund", "update_address"):
        return _r(False, f"Tool not permitted: {tool}")
    if not order:
        return _r(False, "No matching order found")
    if order["customer_email"].lower() != customer_email.lower():
        return _r(False, "Customer email does not match the order (identity check failed)")

    if tool == "issue_refund":
        amount = decision.get("amount") or order["total"]
        if amount <= 0:
            return _r(False, "Refund amount must be positive")
        if order["status"] != "delivered":
            return _r(False, "Refunds are only available for delivered orders")
        if order["days_since_delivery"] > REFUND_WINDOW_DAYS:
            return _r(False, f"Outside the {REFUND_WINDOW_DAYS}-day refund window")
        if amount > order["total"] - order["refunded"]:
            return _r(False, "Refund exceeds refundable amount")
        if amount > REFUND_AUTO_LIMIT:
            return _r(True, f"Refund above auto-limit ({REFUND_AUTO_LIMIT})", needs_approval=True)

    if tool == "update_address" and order["status"] != "processing":
        return _r(False, "Address can only be changed while the order is processing")

    return _r(True, "Within policy")
