from typing import Literal, Optional

from pydantic import BaseModel, Field


class Classification(BaseModel):
    intent: Literal["order_status", "refund", "address_change", "other"]
    order_id: Optional[str] = Field(None, description="Order ID like A1001, if mentioned")


class Decision(BaseModel):
    action: Literal["use_tool", "clarify", "escalate"]
    tool_name: Optional[Literal["order_status", "issue_refund", "update_address"]] = None
    order_id: Optional[str] = None
    amount: Optional[float] = Field(None, description="Refund amount; omit for full refund")
    new_address: Optional[str] = None
    reply_draft: Optional[str] = Field(None, description="Question to ask the customer when action=clarify")
    reason: str = Field(description="One-sentence justification")
