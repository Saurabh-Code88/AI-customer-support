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
from support_agent.auth import issue_token  # noqa: E402


def auth_headers(role="customer", email="a@example.com"):
    token, _ = issue_token(role, email if role == "customer" else None)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(checkpointer):
    tools.reset()
    return TestClient(create_app(build_graph(FakeLLM(), FakeLLM(), checkpointer)), headers=auth_headers())


def test_health_and_index(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "Support Resolution Agent" in r.text
    assert "Sandbox with fake data. Do not enter real personal information." in r.text


def test_request_size_and_security_headers(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    oversized = client.post("/tickets", content=b"x" * 17000, headers={"Content-Type": "application/json"})
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"


def test_cors_allows_only_configured_frontend_origin(client):
    allowed = client.options("/tickets", headers={
        "Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST",
    })
    denied = client.options("/tickets", headers={
        "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST",
    })
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert "access-control-allow-origin" not in denied.headers


def test_ticket_rate_limit_is_applied_per_ip(client, monkeypatch):
    import support_agent.api as api

    monkeypatch.setattr(api, "TICKET_RATE_LIMIT_PER_IP", 1)
    monkeypatch.setattr(api, "TICKET_RATE_LIMIT_PER_SESSION", 10)
    assert client.post("/tickets", json={"message": "Where is order A1001?"}).status_code == 200
    blocked = client.post("/tickets", json={"message": "Where is order A1001?"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "ticket_rate_limited"


def test_ticket_rate_limit_is_applied_per_session(client, monkeypatch):
    import support_agent.api as api

    monkeypatch.setattr(api, "TICKET_RATE_LIMIT_PER_IP", 10)
    monkeypatch.setattr(api, "TICKET_RATE_LIMIT_PER_SESSION", 1)
    token_a = auth_headers()
    token_b = auth_headers()
    assert client.post("/tickets", json={"message": "Where is order A1001?"}, headers=token_a).status_code == 200
    assert client.post("/tickets", json={"message": "Where is order A1001?"}, headers=token_b).status_code == 200
    blocked = client.post("/tickets", json={"message": "Where is order A1001?"}, headers=token_a)
    assert blocked.status_code == 429


def test_daily_llm_cap_returns_friendly_response(client, monkeypatch):
    monkeypatch.setattr("support_agent.api.database.daily_llm_capacity_available", lambda: False)
    response = client.post("/tickets", json={"message": "Where is order A1001?"})
    assert response.status_code == 429
    assert response.json()["error"] == {
        "code": "demo_limit_reached", "message": "Demo limit reached. Try again later.",
    }


def test_quota_counter_enforces_limit_and_resets_by_window():
    from support_agent.database import consume_quota

    assert consume_quota("test_scope", "hashed-test-key", limit=1, window_seconds=60, now=120)
    assert not consume_quota("test_scope", "hashed-test-key", limit=1, window_seconds=60, now=121)
    assert consume_quota("test_scope", "hashed-test-key", limit=1, window_seconds=60, now=180)


def test_demo_reset_has_per_session_cooldown(client):
    first = auth_headers("agent")
    assert client.post("/demo/reset", headers=first).status_code == 200
    blocked = client.post("/demo/reset", headers=first)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "reset_rate_limited"
    assert client.post("/demo/reset", headers=auth_headers("agent")).status_code == 200


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


def test_json_logs_mask_email_addresses():
    formatter = JsonFormatter()
    record = logging.makeLogRecord({"name": "support_agent.test", "msg": "customer a.smith@example.com submitted", "levelno": logging.INFO, "levelname": "INFO"})
    entry = json.loads(formatter.format(record))
    assert entry["message"] == "customer a***@example.com submitted"


def test_ticket_validation_uses_consistent_error_body(client):
    response = client.post("/tickets", json={"message": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "Invalid request."

    too_long = client.post("/tickets", json={"message": "x" * 2001})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "validation_error"


def test_ticket_rejects_invalid_token_email(client):
    response = client.post("/tickets", json={"message": "Please check my order"}, headers=auth_headers("customer", "not-an-email"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_small_refund_resolves(client):
    r = client.post("/tickets", json={"message": "Please refund order A1001"})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "resolved" and body["approval"] is None and body["reply"]


def test_large_refund_needs_approval_then_approve(client):
    r = client.post("/tickets", json={"message": "Please refund order A1002"}, headers=auth_headers("customer", "b@example.com"))
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["approval"]["proposed_action"]["tool_name"] == "issue_refund"
    tid = body["ticket_id"]

    assert client.get(f"/tickets/{tid}", headers=auth_headers("customer", "b@example.com")).json()["status"] == "pending_approval"
    done = client.post(f"/tickets/{tid}/approval", json={"decision": "approve"}, headers=auth_headers("agent")).json()
    assert done["status"] == "resolved"
    assert tools.get_order("A1002")["refunded"] == 5999.0


def test_large_refund_reject_escalates(client):
    tid = client.post("/tickets", json={"message": "Please refund order A1002"}, headers=auth_headers("customer", "b@example.com")).json()["ticket_id"]
    done = client.post(f"/tickets/{tid}/approval", json={"decision": "reject"}, headers=auth_headers("agent")).json()
    assert done["status"] == "escalated"
    assert tools.get_order("A1002")["refunded"] == 0


def test_approval_unknown_ticket_returns_consistent_404(client):
    response = client.post("/tickets/T-nope/approval", json={"decision": "approve"}, headers=auth_headers("agent"))

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "ticket_not_found", "message": "Ticket not found"}
    }


def test_approval_for_resolved_ticket_returns_consistent_409(client):
    ticket = client.post(
        "/tickets",
        json={"message": "Please refund order A1001"},
    ).json()

    response = client.post(
        f"/tickets/{ticket['ticket_id']}/approval", json={"decision": "approve"}, headers=auth_headers("agent")
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
        json={"message": "Please refund order A1002"},
        headers=auth_headers("customer", "b@example.com"),
    ).json()
    url = f"/tickets/{ticket['ticket_id']}/approval"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(url, json={"decision": "approve"}, headers=auth_headers("agent")),
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
        json={"message": "Please refund order A1002"},
        headers=auth_headers("customer", "b@example.com"),
    ).json()
    url = f"/tickets/{ticket['ticket_id']}/approval"

    assert client.post(url, json={"decision": "approve"}, headers=auth_headers("agent")).status_code == 200
    response = client.post(url, json={"decision": "reject"}, headers=auth_headers("agent"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_not_pending"
    assert tools.get_order("A1002")["refunded"] == 5999.0


def test_validation_and_demo(client):
    assert client.post("/tickets", json={"message": "x"}).status_code == 422
    assert client.post("/tickets", json={"message": "hello there", "email": "a@example.com"}).status_code == 422
    assert len(client.get("/demo/orders", headers=auth_headers("agent")).json()) == 5
    tools.issue_refund(order_id="A1001", amount=100, ticket_id="T-demo-reset")
    tools.update_address(order_id="A1003", new_address="Changed address")
    assert client.post("/demo/reset", headers=auth_headers("agent")).json() == {"ok": True}
    orders = client.get("/demo/orders", headers=auth_headers("agent")).json()
    assert set(orders[0]) == {"order_id", "customer_email", "status", "total", "refunded"}
    assert next(order for order in orders if order["order_id"] == "A1001")["refunded"] == 0.0
    assert tools.get_order("A1003")["address"] == "44 Park Street, Kolkata"


def test_streaming_ticket_queue_detail_and_analytics(client):
    streamed = client.post("/tickets/stream", json={"message": "Please refund order A1002"}, headers=auth_headers("customer", "b@example.com"))
    assert streamed.status_code == 200
    assert "event: progress" in streamed.text
    assert "event: complete" in streamed.text
    assert '"status": "pending_approval"' in streamed.text

    queued = client.get("/agent/approvals", headers=auth_headers("agent")).json()
    assert len(queued) == 1
    item = queued[0]
    assert item["decision"]["tool_name"] == "issue_refund"
    assert item["policy_result"]["needs_approval"] is True
    assert item["order"]["order_id"] == "A1002"

    ticket_id = item["ticket_id"]
    detail = client.get(f"/tickets/{ticket_id}", headers=auth_headers("customer", "b@example.com")).json()
    assert detail["policy_result"]["needs_approval"] is True
    assert detail["audit"]
    assert client.get("/analytics", headers=auth_headers("agent")).json()["total_tickets"] == 1

    approved = client.post(f"/tickets/{ticket_id}/approval", json={"decision": "approve"}, headers=auth_headers("agent"))
    assert approved.json()["status"] == "resolved"
    assert client.get("/agent/approvals", headers=auth_headers("agent")).json() == []
    metrics = client.get("/analytics", headers=auth_headers("agent")).json()
    assert metrics["auto_resolution_rate"] == 0.0
    assert metrics["approval_rate"] == 100.0
    assert client.get("/tickets", headers=auth_headers("customer", "b@example.com")).json()[0]["ticket_id"] == ticket_id
    final_detail = client.get(f"/tickets/{ticket_id}", headers=auth_headers("customer", "b@example.com")).json()
    assert final_detail["audit"][-2:] == ["respond: customer reply prepared", "log: audit record written"]


def test_streaming_small_ticket_includes_all_graph_steps(client):
    response = client.post("/tickets/stream", json={"message": "Where is order A1001?"})
    assert response.status_code == 200
    for event_name in ("classify", "retrieve_context", "decide", "policy_check", "execute_tool", "respond", "log"):
        assert f'"name": "{event_name}"' in response.text


def test_missing_token_is_401(client):
    response = client.get("/analytics", headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_token"


def test_expired_token_is_401(client, monkeypatch):
    import time
    import support_agent.auth as auth

    real_time = time.time
    monkeypatch.setattr(auth.time, "time", lambda: 0)
    token, _ = issue_token("customer", "a@example.com")
    monkeypatch.setattr(auth.time, "time", real_time)
    response = client.get("/tickets", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "token_expired"


def test_customer_cannot_access_agent_routes(client):
    response = client.get("/analytics")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


def test_customer_ticket_access_is_scoped_to_token_identity(client):
    ticket = client.post("/tickets", json={"message": "Where is order A1001?"}).json()
    second = client.post("/tickets", json={"message": "Where is order A1002?"}, headers=auth_headers("customer", "b@example.com")).json()

    assert [item["ticket_id"] for item in client.get("/tickets").json()] == [ticket["ticket_id"]]
    assert client.get(f"/tickets/{second['ticket_id']}").status_code == 404


def test_request_body_cannot_override_customer_identity(client):
    response = client.post("/tickets", json={"message": "Please refund order A1002", "email": "b@example.com"})
    assert response.status_code == 422
    assert tools.get_order("A1002")["refunded"] == 0


def test_policy_identity_check_uses_customer_token_email(client):
    from support_agent.policy import check

    ticket = client.post("/tickets", json={"message": "Please refund order A1002"}).json()
    detail = client.get(f"/tickets/{ticket['ticket_id']}").json()

    assert detail["email"] == "a@example.com"
    result = check({"tool_name": "issue_refund", "amount": 5999}, tools.get_order("A1002"), detail["email"])
    assert result["allowed"] is False
    assert "email does not match" in result["reason"].lower()
    assert tools.get_order("A1002")["refunded"] == 0


def test_session_issuance_uses_seeded_customer_and_agent_roles(client):
    customers = client.get("/auth/customers").json()
    assert len(customers) == 5
    customer = client.post("/auth/session", json={"role": "customer", "customer_email": "a@example.com"})
    agent = client.post("/auth/session", json={"role": "agent"})
    assert customer.status_code == agent.status_code == 200
    assert customer.json()["role"] == "customer" and customer.json()["email"] == "a@example.com"
    assert agent.json()["role"] == "agent" and agent.json()["email"] is None


def test_production_requires_signing_secret(monkeypatch):
    from support_agent.auth import validate_auth_configuration

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET is required in production"):
        validate_auth_configuration()


def test_env_production_requires_provider_secrets(monkeypatch):
    from support_agent.auth import validate_auth_configuration

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY, GROQ_API_KEY are required"):
        validate_auth_configuration()
