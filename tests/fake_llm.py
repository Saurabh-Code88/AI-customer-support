"""Rule-based fake models so the graph logic can be tested offline with no API keys."""
import re
from types import SimpleNamespace

from support_agent.schemas import Classification, Decision


class _Structured:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, msgs):
        text = msgs[-1][1]
        if self.schema is Decision:
            text = next(m[1] for m in reversed(msgs) if "Intent:" in m[1])
        order = re.search(r"A\d{4}", text)
        order_id = order.group(0) if order else None
        low = text.lower()
        if self.schema is Classification:
            if "refund" in low:
                intent = "refund"
            elif "address" in low:
                intent = "address_change"
            elif "where is" in low or "status" in low or "kahan" in low:
                intent = "order_status"
            else:
                intent = "other"
            return Classification(intent=intent, order_id=order_id)
        intent = re.search(r"Intent: (\w+)", text).group(1)
        message = re.search(r"Message: (.*)", text).group(1)
        if intent == "other":
            return Decision(action="escalate", reason="out of scope")
        if not order_id:
            return Decision(action="clarify", reply_draft="What is your order ID?", reason="missing order id")
        if intent == "refund":
            amt = re.search(r"refund (?:only )?(\d{3,})\b", message.lower())
            return Decision(action="use_tool", tool_name="issue_refund", order_id=order_id,
                            amount=float(amt.group(1)) if amt else None, reason="refund")
        if intent == "address_change":
            return Decision(action="use_tool", tool_name="update_address", order_id=order_id,
                            new_address=(re.search(r"A\d{4} to (.*)", message).group(1).rstrip(".") if re.search(r"A\d{4} to ", message) else None),
                            reason="address")
        return Decision(action="use_tool", tool_name="order_status", order_id=order_id, reason="status")


class FakeLLM:
    def with_structured_output(self, schema):
        return _Structured(schema)

    def invoke(self, msgs):
        return SimpleNamespace(content="Done! Your request has been processed.")
