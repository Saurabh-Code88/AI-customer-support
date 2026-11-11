"""FastAPI backend + tiny web UI for the support resolution agent.

Run:  python -m uvicorn support_agent.api:app --reload      (from the project folder, with PYTHONPATH=src)
Open: http://127.0.0.1:8000
"""
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

# Load env and strict checkpoint settings before importing LangGraph modules.
from .config import LANGGRAPH_STRICT_MSGPACK as _strict_msgpack  # noqa: F401

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import tools
from . import database
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


def _approval_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_app(graph=None) -> FastAPI:
    """Pass a prebuilt graph for tests. Otherwise the real graph is built on first use."""
    holder = {
        "graph": graph,
        "checkpointer": None,
        "approval_locks": {},
        "approval_locks_guard": Lock(),
    }

    @asynccontextmanager
    async def lifespan(_app):
        database.run_migrations()
        from .seed import seed_orders

        seed_orders()
        if holder["graph"] is not None:
            yield
            return
        with database.checkpointer_context() as checkpointer:
            holder["checkpointer"] = checkpointer
            yield

    app = FastAPI(title="Support Resolution Agent", lifespan=lifespan)

    def get_graph():
        if holder["graph"] is None:
            try:
                from .config import get_fast_llm, get_main_llm
                from .graph import build_graph

                holder["graph"] = build_graph(
                    get_fast_llm(), get_main_llm(), holder["checkpointer"]
                )
            except Exception as e:  # noqa: BLE001 - usually missing API keys
                raise HTTPException(503, f"Could not start the agent (check API keys in .env): {e}")
        return holder["graph"]

    def cfg(ticket_id: str):
        return {"configurable": {"thread_id": ticket_id}}

    def approval_lock(ticket_id: str):
        with holder["approval_locks_guard"]:
            return holder["approval_locks"].setdefault(ticket_id, Lock())

    def remember(ticket_id: str, values: dict, *, created_at=None, steps=None):
        pending = values.get("approval") == "pending" or bool(values.get("__interrupt__"))
        status = "pending_approval" if pending else values.get("resolution", "processing")
        database.upsert_ticket(
            ticket_id,
            values.get("message", ""),
            values.get("customer_email", ""),
            status=status,
            values=values,
            steps=steps,
            created_at=created_at,
            audit=values.get("audit", []),
        )

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
        database.create_ticket(ticket_id, body.message.strip(), body.email.strip())
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
        database.create_ticket(ticket_id, initial["message"], initial["customer_email"])
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
        info = database.get_ticket(ticket_id)
        if info is None:
            raise HTTPException(404, "Ticket not found")
        with ticket_context(ticket_id):
            g = get_graph()
            snap = g.get_state(cfg(ticket_id))
            pending = snap.tasks[0].interrupts[0].value if snap.next and snap.tasks and snap.tasks[0].interrupts else None
            values = dict(snap.values or info["values"])
            logger.info("Ticket detail requested")
            values["audit"] = info["audit"]
            return {**_shape(ticket_id, values, pending), "message": info["message"], "email": info["email"], "steps": info["steps"], "policy_result": values.get("policy_result"), "decision": values.get("decision"), "order": values.get("order"), "created_at": info["created_at"], "resolved_at": info["resolved_at"]}

    @app.post("/tickets/{ticket_id}/approval")
    def decide_approval(ticket_id: str, body: ApprovalIn):
        with approval_lock(ticket_id):
            info = database.get_ticket(ticket_id)
            if info is None:
                return _approval_error(404, "ticket_not_found", "Ticket not found")
            if info.get("status") != "pending_approval":
                return _approval_error(409, "approval_not_pending", "Ticket is not awaiting approval")

            with ticket_context(ticket_id):
                g = get_graph()
                snap = g.get_state(cfg(ticket_id))
                awaiting_interrupt = bool(
                    snap.next
                    and snap.tasks
                    and snap.tasks[0].interrupts
                )
                if not awaiting_interrupt:
                    return _approval_error(409, "approval_not_pending", "Ticket is not awaiting approval")

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
        items = []
        for info in database.list_tickets(status="pending_approval"):
            values = info["values"]
            decision = values.get("decision", {})
            items.append({"ticket_id": info["ticket_id"], "message": info["message"], "email": info["email"], "created_at": info["created_at"], "decision": decision, "policy_result": values.get("policy_result"), "order": values.get("order")})
        return items

    @app.get("/tickets")
    def list_tickets():
        return [{"ticket_id": x["ticket_id"], "message": x["message"], "email": x["email"], "status": x["status"], "created_at": x["created_at"]} for x in database.list_tickets()]

    @app.get("/analytics")
    def analytics():
        return database.analytics()

    # ---- demo helpers (mock data only) ----
    @app.get("/demo/orders")
    def demo_orders():
        return [
            {
                key: order[key]
                for key in ("order_id", "customer_email", "status", "total", "refunded")
            }
            for order in tools.list_orders()
        ]

    @app.post("/demo/reset")
    def demo_reset():
        tools.reset()
        return {"ok": True}

    return app


app = create_app()
