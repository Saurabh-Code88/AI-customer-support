"""CLI: python -m support_agent.main --email a@example.com "Please refund order A1001" """
import argparse
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .config import get_fast_llm, get_main_llm
from .graph import build_graph
from .logging_config import ticket_context


def run_ticket(graph, message: str, email: str, ticket_id: str | None = None, approver=None):
    ticket_id = ticket_id or f"T-{uuid.uuid4().hex[:6]}"
    cfg = {"configurable": {"thread_id": ticket_id}}
    with ticket_context(ticket_id):
        result = graph.invoke({"ticket_id": ticket_id, "message": message, "customer_email": email}, cfg)
        approvals = 0
        while "__interrupt__" in result:
            approvals += 1
            payload = result["__interrupt__"][0].value
            answer = approver(payload) if approver else input(f"\n[APPROVAL NEEDED] {payload}\n> ")
            result = graph.invoke(Command(resume=answer), cfg)
    result["_approvals"] = approvals
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--email", required=True)
    args = ap.parse_args()
    graph = build_graph(get_fast_llm(), get_main_llm(), MemorySaver())
    out = run_ticket(graph, args.message, args.email)
    print("\nResolution:", out["resolution"])
    print("Reply:", out["reply"])
    print("Audit:")
    for line in out["audit"]:
        print("  -", line)


if __name__ == "__main__":
    main()
