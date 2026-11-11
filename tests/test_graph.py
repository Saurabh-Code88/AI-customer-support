import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402

from support_agent import tools  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402
from support_agent.main import run_ticket  # noqa: E402

TICKETS = json.loads((ROOT / "evals" / "tickets.json").read_text())


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_routing(t, checkpointer):
    tools.reset()
    for oid, amt in t.get("preset_refunded", {}).items():
        tools.issue_refund(order_id=oid, amount=amt, ticket_id=f"fixture-{t['id']}-{oid}")
    graph = build_graph(FakeLLM(), FakeLLM(), checkpointer)
    out = run_ticket(graph, t["message"], t["email"], ticket_id=t["id"], approver=lambda p: "approve")
    assert out["resolution"] == t["expect_resolution"]
    assert (out["_approvals"] > 0) == t["expect_approval"]
    if "expect_refund_amount" in t:
        assert out["tool_result"]["refunded"] == t["expect_refund_amount"]


def test_rejected_approval_escalates(checkpointer):
    tools.reset()
    graph = build_graph(FakeLLM(), FakeLLM(), checkpointer)
    out = run_ticket(graph, "Please refund order A1002", "b@example.com", approver=lambda p: "reject")
    assert out["resolution"] == "escalated"
    assert tools.get_order("A1002")["refunded"] == 0


class _FlakyClarifyLLM(FakeLLM):
    """Decider that wrongly asks for clarification on its first call, then behaves."""
    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema):
        base = FakeLLM().with_structured_output(schema)
        outer = self

        class _S:
            def invoke(self, msgs):
                from support_agent.schemas import Decision
                if schema is Decision:
                    outer.calls += 1
                    if outer.calls == 1:
                        return Decision(action="clarify", reply_draft="Why?", reason="flaky")
                return base.invoke(msgs)

        return _S()


def test_wrong_clarify_is_retried(checkpointer):
    tools.reset()
    flaky = _FlakyClarifyLLM()
    graph = build_graph(FakeLLM(), flaky, checkpointer)
    out = run_ticket(graph, "Please refund order A1001.", "a@example.com")
    assert out["resolution"] == "resolved"
    assert flaky.calls == 2


def test_refund_retry_after_lost_tool_response_applies_once(monkeypatch, checkpointer):
    tools.reset()
    original_run_tool = tools.run_tool
    calls = 0

    def lose_first_refund_response(name, **kwargs):
        nonlocal calls
        result = original_run_tool(name, **kwargs)
        if name == "issue_refund":
            calls += 1
            if calls == 1:
                raise TimeoutError("refund succeeded but response was lost")
        return result

    monkeypatch.setattr("support_agent.graph.T.run_tool", lose_first_refund_response)
    graph = build_graph(FakeLLM(), FakeLLM(), checkpointer)
    out = run_ticket(
        graph,
        "Please refund order A1001.",
        "a@example.com",
        ticket_id="T-retry01",
    )

    assert out["resolution"] == "resolved"
    assert calls == 2
    assert tools.get_order("A1001")["refunded"] == 1499.0
