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


class _FailingFakeLLM(FakeLLM):
    def with_structured_output(self, schema):
        class _Failing:
            def invoke(self, _msgs):
                raise TimeoutError("provider unavailable")
        return _Failing()


class _MalformedFakeLLM(FakeLLM):
    def with_structured_output(self, schema):
        class _Malformed:
            def invoke(self, _msgs):
                return {"unexpected": "shape"}
        return _Malformed()


class _MalformedDecisionFakeLLM(FakeLLM):
    def with_structured_output(self, schema):
        if schema.__name__ != "Decision":
            return super().with_structured_output(schema)
        class _Malformed:
            def invoke(self, _msgs):
                return {"unexpected": "shape"}
        return _Malformed()


def test_provider_failure_retries_then_falls_back(monkeypatch, checkpointer):
    tools.reset()
    monkeypatch.setattr("support_agent.graph.time.sleep", lambda _delay: None)
    graph = build_graph(_FailingFakeLLM(), FakeLLM(), checkpointer, fast_fallback=FakeLLM())
    out = run_ticket(graph, "Please refund order A1001.", "a@example.com")
    assert out["resolution"] == "resolved"
    assert tools.get_order("A1001")["refunded"] == 1499.0


def test_repeated_malformed_classification_escalates(checkpointer, monkeypatch):
    tools.reset()
    monkeypatch.setattr("support_agent.graph.time.sleep", lambda _delay: None)
    graph = build_graph(
        _MalformedFakeLLM(), FakeLLM(), checkpointer, fast_fallback=_MalformedFakeLLM()
    )
    out = run_ticket(graph, "Please refund order A1001.", "a@example.com")
    assert out["resolution"] == "escalated"
    assert "escalated for human review" in " ".join(out["audit"])
    assert "Please refund" not in " ".join(out["audit"])


def test_repeated_malformed_decision_escalates(checkpointer, monkeypatch):
    tools.reset()
    monkeypatch.setattr("support_agent.graph.time.sleep", lambda _delay: None)
    graph = build_graph(
        FakeLLM(), _MalformedDecisionFakeLLM(), checkpointer,
        main_fallback=_MalformedDecisionFakeLLM(),
    )
    out = run_ticket(graph, "Please refund order A1001.", "a@example.com")
    assert out["resolution"] == "escalated"
    assert "decide: escalate" in " ".join(out["audit"])


def test_daily_call_cap_escalates_with_friendly_message(checkpointer):
    tools.reset()
    graph = build_graph(FakeLLM(), FakeLLM(), checkpointer, llm_call_guard=lambda: False)
    out = run_ticket(graph, "Where is order A1001?", "a@example.com")
    assert out["resolution"] == "escalated"
    assert out["reply"] == "Demo limit reached. Try again later."
