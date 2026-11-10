"""FastAPI backend + tiny web UI for the support resolution agent.

Run:  python -m uvicorn support_agent.api:app --reload      (from the project folder, with PYTHONPATH=src)
Open: http://127.0.0.1:8000
"""
import json
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import tools
from .database import database_ready
from .logging_config import configure_logging, ticket_context

STATIC = Path(__file__).parent / "static"
logger = configure_logging()


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
    holder = {"graph": graph, "tickets": {}}

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

    def remember(ticket_id: str, values: dict, *, created_at=None, steps=None):
        prior = holder["tickets"].get(ticket_id, {})
        pending = values.get("approval") == "pending" or bool(values.get("__interrupt__"))
        status = "pending_approval" if pending else values.get("resolution", prior.get("status", "processing"))
        resolved_at = prior.get("resolved_at")
        if status in ("resolved", "escalated", "needs_info") and not resolved_at:
            resolved_at = time.time()
        holder["tickets"][ticket_id] = {
            **prior,
            "ticket_id": ticket_id,
            "message": values.get("message", prior.get("message", "")),
            "email": values.get("customer_email", prior.get("email", "")),
            "status": status,
            "created_at": created_at or prior.get("created_at", time.time()),
            "resolved_at": resolved_at,
            "steps": steps if steps is not None else prior.get("steps", []),
            "values": values,
        }

    def current_values(g, ticket_id: str, fallback=None):
        snap = g.get_state(cfg(ticket_id))
        values = dict(snap.values or fallback or {})
        if snap.next and snap.tasks and snap.tasks[0].interrupts:
            values["__interrupt__"] = [{"value": snap.tasks[0].interrupts[0].value}]
        return values

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ready")
    def ready():
        if not database_ready():
            logger.warning("Readiness probe failed: database unavailable")
            raise HTTPException(status_code=503, detail="Database unavailable")
        return {"ok": True, "database": "ready"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/tickets")
    def create_ticket(body: TicketIn):
        ticket_id = f"T-{uuid.uuid4().hex[:6]}"
        created_at = time.time()
        with ticket_context(ticket_id):
            logger.info("Ticket processing started")
            g = get_graph()
            result = g.invoke(
                {"ticket_id": ticket_id, "message": body.message.strip(), "customer_email": body.email.strip()},
                cfg(ticket_id),
            )
            pending = result["__interrupt__"][0].value if "__interrupt__" in result else None
            remember(ticket_id, {**result, "message": body.message.strip(), "customer_email": body.email.strip(), "approval": "pending" if pending else result.get("approval")}, created_at=created_at)
            logger.info("Ticket processing paused for approval" if pending else "Ticket processing completed")
        return _shape(ticket_id, result, pending)

    @app.post("/tickets/stream")
    def create_ticket_stream(body: TicketIn):
        ticket_id = f"T-{uuid.uuid4().hex[:6]}"
        created_at = time.time()
        initial = {"ticket_id": ticket_id, "message": body.message.strip(), "customer_email": body.email.strip()}
        g = get_graph()

        def events():
            steps = []
            labels = {
                "classify": "Classify request", "retrieve_context": "Retrieve order and policy",
                "decide": "Propose action", "policy_check": "Check policy",
                "human_approval": "Await human approval", "execute_tool": "Execute action",
                "respond": "Prepare response", "escalate": "Escalate to support", "log": "Record audit trail",
            }
            logger.info("Streaming ticket processing started", extra={"ticket_id": ticket_id})
            yield f"event: ticket\ndata: {json.dumps({'ticket_id': ticket_id})}\n\n"
            try:
                for update in g.stream(initial, cfg(ticket_id), stream_mode="updates"):
                    for node in update:
                        step = {"name": node, "label": labels.get(node, node.replace("_", " ").title()), "status": "complete", "at": time.time()}
                        steps.append(step)
                        logger.info("Graph step completed", extra={"ticket_id": ticket_id, "graph_step": node})
                        yield f"event: progress\ndata: {json.dumps(step)}\n\n"
                values = current_values(g, ticket_id)
                pending = values.get("__interrupt__", [None])[0]
                pending_payload = pending.get("value") if pending else None
                values.update({"message": initial["message"], "customer_email": initial["customer_email"], "approval": "pending" if pending_payload else values.get("approval")})
                remember(ticket_id, values, created_at=created_at, steps=steps)
                logger.info("Ticket processing paused for approval" if pending_payload else "Ticket processing completed", extra={"ticket_id": ticket_id})
                yield f"event: complete\ndata: {json.dumps(_shape(ticket_id, values, pending_payload))}\n\n"
            except Exception:
                logger.exception("Ticket streaming failed", extra={"ticket_id": ticket_id})
                yield f"event: error\ndata: {json.dumps({'message': 'Ticket processing failed. Please try again.'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str):
        with ticket_context(ticket_id):
            g = get_graph()
            snap = g.get_state(cfg(ticket_id))
            if not snap.values:
                raise HTTPException(404, "Ticket not found")
            pending = snap.tasks[0].interrupts[0].value if snap.next and snap.tasks and snap.tasks[0].interrupts else None
            values = dict(snap.values)
            info = holder["tickets"].get(ticket_id, {})
            logger.info("Ticket detail requested")
            return {**_shape(ticket_id, values, pending), "message": info.get("message", values.get("message", "")), "email": info.get("email", values.get("customer_email", "")), "steps": info.get("steps", []), "policy_result": values.get("policy_result"), "decision": values.get("decision"), "order": values.get("order"), "created_at": info.get("created_at"), "resolved_at": info.get("resolved_at")}

    @app.post("/tickets/{ticket_id}/approval")
    def decide_approval(ticket_id: str, body: ApprovalIn):
        with ticket_context(ticket_id):
            g = get_graph()
            snap = g.get_state(cfg(ticket_id))
            if not snap.values:
                raise HTTPException(404, "Ticket not found")
            if not snap.next:
                raise HTTPException(409, "This ticket is not waiting for approval")
            result = g.invoke(Command(resume=body.decision), cfg(ticket_id))
            pending = result["__interrupt__"][0].value if "__interrupt__" in result else None
            values = current_values(g, ticket_id, result)
            if pending:
                values["approval"] = "pending"
            remember(ticket_id, values)
            logger.info("Approval decision recorded", extra={"approval_decision": body.decision})
        return _shape(ticket_id, result, pending)

    @app.get("/agent/approvals")
    def approval_queue():
        g = get_graph()
        items = []
        for ticket_id, info in holder["tickets"].items():
            if info.get("status") != "pending_approval":
                continue
            values = current_values(g, ticket_id, info.get("values"))
            decision = values.get("decision", {})
            items.append({"ticket_id": ticket_id, "message": info.get("message"), "email": info.get("email"), "created_at": info.get("created_at"), "decision": decision, "policy_result": values.get("policy_result"), "order": values.get("order")})
        return items

    @app.get("/tickets")
    def list_tickets():
        return [{"ticket_id": x["ticket_id"], "message": x["message"], "email": x["email"], "status": x["status"], "created_at": x["created_at"]} for x in sorted(holder["tickets"].values(), key=lambda x: x["created_at"], reverse=True)]

    @app.get("/analytics")
    def analytics():
        records = list(holder["tickets"].values())
        total = len(records)
        auto_resolved = sum(
            x["status"] == "resolved" and not x.get("values", {}).get("policy_result", {}).get("needs_approval")
            for x in records
        )
        escalated = sum(x["status"] == "escalated" for x in records)
        approval_needed = sum(bool(x.get("values", {}).get("policy_result", {}).get("needs_approval")) for x in records)
        approval_completed = sum(bool(x.get("values", {}).get("approval") in ("approved", "rejected")) for x in records)
        durations = [x["resolved_at"] - x["created_at"] for x in records if x.get("resolved_at")]
        return {"total_tickets": total, "auto_resolution_rate": round(auto_resolved / total * 100, 1) if total else 0, "escalation_rate": round(escalated / total * 100, 1) if total else 0, "approval_rate": round(approval_completed / approval_needed * 100, 1) if approval_needed else 0, "average_resolution_seconds": round(sum(durations) / len(durations), 1) if durations else 0}

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
