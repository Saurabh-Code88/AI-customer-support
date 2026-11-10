"""FastAPI backend + tiny web UI for the support resolution agent.

Run:  python -m uvicorn support_agent.api:app --reload      (from the project folder, with PYTHONPATH=src)
Open: http://127.0.0.1:8000
"""
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import tools

STATIC = Path(__file__).parent / "static"


class TicketIn(BaseModel):
    message: str = Field(min_length=3, max_length=2000)
    email: str = Field(min_length=3, max_length=200)


class ApprovalIn(BaseModel):
    decision: Literal["approve", "reject"]


def _shape(ticket_id: str, values: dict, pending_payload=None) -> dict:
    """One consistent response format for every endpoint."""
    return {
        "ticket_id": ticket_id,
        "status": "pending_approval" if pending_payload else values.get("resolution"),
        "reply": None if pending_payload else values.get("reply"),
        "approval": pending_payload,
        "audit": values.get("audit", []),
    }


def create_app(graph=None) -> FastAPI:
    """Pass a prebuilt graph for tests. Otherwise the real graph is built on first use."""
    app = FastAPI(title="Support Resolution Agent")
    holder = {"graph": graph}

    def get_graph():
        if holder["graph"] is None:
            try:
                from .config import get_fast_llm, get_main_llm
                from .graph import build_graph

                holder["graph"] = build_graph(get_fast_llm(), get_main_llm(), MemorySaver())
            except Exception as e:  # noqa: BLE001 - usually missing API keys
                raise HTTPException(503, f"Could not start the agent (check API keys in .env): {e}")
        return holder["graph"]

    def cfg(ticket_id: str):
        return {"configurable": {"thread_id": ticket_id}}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/tickets")
    def create_ticket(body: TicketIn):
        g = get_graph()
        ticket_id = f"T-{uuid.uuid4().hex[:6]}"
        result = g.invoke(
            {"ticket_id": ticket_id, "message": body.message.strip(), "customer_email": body.email.strip()},
            cfg(ticket_id),
        )
        pending = result["__interrupt__"][0].value if "__interrupt__" in result else None
        return _shape(ticket_id, result, pending)

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str):
        g = get_graph()
        snap = g.get_state(cfg(ticket_id))
        if not snap.values:
            raise HTTPException(404, "Ticket not found")
        pending = snap.tasks[0].interrupts[0].value if snap.next and snap.tasks and snap.tasks[0].interrupts else None
        return _shape(ticket_id, snap.values, pending)

    @app.post("/tickets/{ticket_id}/approval")
    def decide_approval(ticket_id: str, body: ApprovalIn):
        g = get_graph()
        snap = g.get_state(cfg(ticket_id))
        if not snap.values:
            raise HTTPException(404, "Ticket not found")
        if not snap.next:
            raise HTTPException(409, "This ticket is not waiting for approval")
        result = g.invoke(Command(resume=body.decision), cfg(ticket_id))
        pending = result["__interrupt__"][0].value if "__interrupt__" in result else None
        return _shape(ticket_id, result, pending)

    # ---- demo helpers (mock data only) ----
    @app.get("/demo/orders")
    def demo_orders():
        return [
            {"order_id": oid, "customer_email": o["customer_email"], "status": o["status"],
             "total": o["total"], "refunded": o["refunded"]}
            for oid, o in tools.ORDERS.items()
        ]

    @app.post("/demo/reset")
    def demo_reset():
        tools.reset()
        return {"ok": True}

    return app


app = create_app()
