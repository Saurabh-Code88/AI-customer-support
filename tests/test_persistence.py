from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from fake_llm import FakeLLM  # noqa: E402
from support_agent import tools  # noqa: E402
from support_agent.database import (  # noqa: E402
    checkpointer_context,
    configure_database,
    current_database_url,
    get_ticket,
)
from support_agent.api import create_app  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402


def test_refund_idempotency_survives_database_reconfiguration():
    url = current_database_url()
    result = tools.issue_refund(order_id="A1001", ticket_id="T-durable1")
    configure_database(url)

    retried = tools.issue_refund(order_id="A1001", ticket_id="T-durable1")

    assert retried == result
    assert tools.get_order("A1001")["refunded"] == 1499.0


def test_pending_approval_survives_checkpointer_reopen():
    with checkpointer_context() as checkpointer:
        first_graph = build_graph(FakeLLM(), FakeLLM(), checkpointer)
        with TestClient(create_app(first_graph)) as client:
            created = client.post(
                "/tickets",
                json={"message": "Please refund order A1002.", "email": "b@example.com"},
            )
            assert created.status_code == 200
            ticket_id = created.json()["ticket_id"]
            assert created.json()["status"] == "pending_approval"

    with checkpointer_context() as reopened_checkpointer:
        resumed_graph = build_graph(FakeLLM(), FakeLLM(), reopened_checkpointer)
        with TestClient(create_app(resumed_graph)) as client:
            detail = client.get(f"/tickets/{ticket_id}")
            assert detail.status_code == 200
            assert detail.json()["status"] == "pending_approval"
            completed = client.post(
                f"/tickets/{ticket_id}/approval", json={"decision": "approve"}
            )

    assert completed.status_code == 200
    assert completed.json()["status"] == "resolved"
    assert tools.get_order("A1002")["refunded"] == 5999.0
    ticket = get_ticket(ticket_id)
    assert ticket["status"] == "resolved"
    assert ticket["audit"][-1] == "log: audit record written"


def test_demo_reset_reseeds_database_orders():
    tools.issue_refund(order_id="A1001", amount=100, ticket_id="T-reset01")
    tools.update_address(order_id="A1003", new_address="Changed address")

    tools.reset()

    assert len(tools.list_orders()) == 5
    assert tools.get_order("A1001")["refunded"] == 0.0
    assert tools.get_order("A1003")["address"] == "44 Park Street, Kolkata"
    assert tools.issue_refund(order_id="A1001", amount=100, ticket_id="T-reset01")["refunded"] == 100.0
    assert tools.get_order("A1001")["refunded"] == 100.0


def test_default_app_lifespan_initializes_persistent_sqlite():
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"ok": True}
    assert len(tools.list_orders()) == 5
