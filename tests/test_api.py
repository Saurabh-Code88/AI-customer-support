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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("support_agent.graph.AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    tools.reset()
    return TestClient(create_app(build_graph(FakeLLM(), FakeLLM(), MemorySaver())))


def test_health_and_index(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "Support Resolution Agent" in r.text


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
