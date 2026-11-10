import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from support_agent import tools  # noqa: E402
from support_agent.api import create_app  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402
from support_agent.logging_config import JsonFormatter, ticket_context  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("support_agent.graph.AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    tools.reset()
    return TestClient(create_app(build_graph(FakeLLM(), FakeLLM(), MemorySaver())))


def test_health_and_index(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "Support Resolution Agent" in r.text


def test_readiness_requires_database(client, monkeypatch):
    monkeypatch.setattr("support_agent.api.database_ready", lambda: True)
    assert client.get("/ready").json() == {"ok": True, "database": "ready"}

    monkeypatch.setattr("support_agent.api.database_ready", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Database unavailable"


def test_json_logs_always_include_ticket_id():
    formatter = JsonFormatter()
    record = logging.makeLogRecord({"name": "support_agent.test", "msg": "step completed", "levelno": logging.INFO, "levelname": "INFO"})
    with ticket_context("T-test01"):
        entry = json.loads(formatter.format(record))
    assert entry["ticket_id"] == "T-test01"
    assert entry["message"] == "step completed"

    system_entry = json.loads(formatter.format(record))
    assert system_entry["ticket_id"] == "system"


def test_small_refund_resolves(client):
    r = client.post("/tickets", json={"message": "Please refund order A1001", "email": "a@example.com"})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "resolved" and body["approval"] is None and body["reply"]


def test_large_refund_needs_approval_then_approve(client):
    r = client.post("/tickets", json={"message": "Please refund order A1002", "email": "b@example.com"})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["approval"]["proposed_action"]["tool_name"] == "issue_refund"
    tid = body["ticket_id"]

    assert client.get(f"/tickets/{tid}").json()["status"] == "pending_approval"
    done = client.post(f"/tickets/{tid}/approval", json={"decision": "approve"}).json()
    assert done["status"] == "resolved"
    assert tools.ORDERS["A1002"]["refunded"] == 5999.0


def test_large_refund_reject_escalates(client):
    tid = client.post("/tickets", json={"message": "Please refund order A1002", "email": "b@example.com"}).json()["ticket_id"]
    done = client.post(f"/tickets/{tid}/approval", json={"decision": "reject"}).json()
    assert done["status"] == "escalated"
    assert tools.ORDERS["A1002"]["refunded"] == 0


def test_approval_errors(client):
    assert client.post("/tickets/T-nope/approval", json={"decision": "approve"}).status_code == 404
    assert client.get("/tickets/T-nope").status_code == 404
    tid = client.post("/tickets", json={"message": "Please refund order A1001", "email": "a@example.com"}).json()["ticket_id"]
    assert client.post(f"/tickets/{tid}/approval", json={"decision": "approve"}).status_code == 409


def test_validation_and_demo(client):
    assert client.post("/tickets", json={"message": "x", "email": "a@example.com"}).status_code == 422
    assert client.post("/tickets", json={"message": "hello there", "email": ""}).status_code == 422
    assert len(client.get("/demo/orders").json()) == 5
    assert client.post("/demo/reset").json() == {"ok": True}


def test_streaming_ticket_queue_detail_and_analytics(client):
    streamed = client.post("/tickets/stream", json={"message": "Please refund order A1002", "email": "b@example.com"})
    assert streamed.status_code == 200
    assert "event: progress" in streamed.text
    assert "event: complete" in streamed.text
    assert '"status": "pending_approval"' in streamed.text

    queued = client.get("/agent/approvals").json()
    assert len(queued) == 1
    item = queued[0]
    assert item["decision"]["tool_name"] == "issue_refund"
    assert item["policy_result"]["needs_approval"] is True
    assert item["order"]["order_id"] == "A1002"

    ticket_id = item["ticket_id"]
    detail = client.get(f"/tickets/{ticket_id}").json()
    assert detail["policy_result"]["needs_approval"] is True
    assert detail["audit"]
    assert client.get("/analytics").json()["total_tickets"] == 1

    approved = client.post(f"/tickets/{ticket_id}/approval", json={"decision": "approve"})
    assert approved.json()["status"] == "resolved"
    assert client.get("/agent/approvals").json() == []
    metrics = client.get("/analytics").json()
    assert metrics["auto_resolution_rate"] == 0.0
    assert metrics["approval_rate"] == 100.0
    assert client.get("/tickets").json()[0]["ticket_id"] == ticket_id
    final_detail = client.get(f"/tickets/{ticket_id}").json()
    assert final_detail["audit"][-2:] == ["respond: customer reply prepared", "log: audit record written"]


def test_streaming_small_ticket_includes_all_graph_steps(client):
    response = client.post("/tickets/stream", json={"message": "Where is order A1001?", "email": "a@example.com"})
    assert response.status_code == 200
    for event_name in ("classify", "retrieve_context", "decide", "policy_check", "execute_tool", "respond", "log"):
        assert f'"name": "{event_name}"' in response.text
