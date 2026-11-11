import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from support_agent import tools  # noqa: E402
from support_agent.api import create_app  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402
from support_agent.logging_config import JsonFormatter, ticket_context  # noqa: E402


@pytest.fixture()
def client(checkpointer):
    tools.reset()
    return TestClient(create_app(build_graph(FakeLLM(), FakeLLM(), checkpointer)))


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
    assert tools.get_order("A1002")["refunded"] == 5999.0


def test_large_refund_reject_escalates(client):
    tid = client.post("/tickets", json={"message": "Please refund order A1002", "email": "b@example.com"}).json()["ticket_id"]
    done = client.post(f"/tickets/{tid}/approval", json={"decision": "reject"}).json()
    assert done["status"] == "escalated"
    assert tools.get_order("A1002")["refunded"] == 0


def test_approval_unknown_ticket_returns_consistent_404(client):
    response = client.post("/tickets/T-nope/approval", json={"decision": "approve"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "ticket_not_found", "message": "Ticket not found"}
    }


def test_approval_for_resolved_ticket_returns_consistent_409(client):
    ticket = client.post(
        "/tickets",
        json={"message": "Please refund order A1001", "email": "a@example.com"},
    ).json()

    response = client.post(
        f"/tickets/{ticket['ticket_id']}/approval", json={"decision": "approve"}
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "approval_not_pending",
            "message": "Ticket is not awaiting approval",
        }
    }


def test_double_approval_is_rejected(client):
    ticket = client.post(
        "/tickets",
        json={"message": "Please refund order A1002", "email": "b@example.com"},
    ).json()
    url = f"/tickets/{ticket['ticket_id']}/approval"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(url, json={"decision": "approve"}),
                range(2),
            )
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "approval_not_pending"
    assert tools.get_order("A1002")["refunded"] == 5999.0


def test_approve_then_reject_is_rejected(client):
    ticket = client.post(
        "/tickets",
        json={"message": "Please refund order A1002", "email": "b@example.com"},
    ).json()
    url = f"/tickets/{ticket['ticket_id']}/approval"

    assert client.post(url, json={"decision": "approve"}).status_code == 200
    response = client.post(url, json={"decision": "reject"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_not_pending"
    assert tools.get_order("A1002")["refunded"] == 5999.0


def test_validation_and_demo(client):
    assert client.post("/tickets", json={"message": "x", "email": "a@example.com"}).status_code == 422
    assert client.post("/tickets", json={"message": "hello there", "email": ""}).status_code == 422
    assert len(client.get("/demo/orders").json()) == 5
    tools.issue_refund(order_id="A1001", amount=100, ticket_id="T-demo-reset")
    tools.update_address(order_id="A1003", new_address="Changed address")
    assert client.post("/demo/reset").json() == {"ok": True}
    orders = client.get("/demo/orders").json()
    assert set(orders[0]) == {"order_id", "customer_email", "status", "total", "refunded"}
    assert next(order for order in orders if order["order_id"] == "A1001")["refunded"] == 0.0
    assert tools.get_order("A1003")["address"] == "44 Park Street, Kolkata"


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
