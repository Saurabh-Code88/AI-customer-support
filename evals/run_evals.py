"""Run the ticket eval set against the real models (Gemini + Groq).
Usage: python evals/run_evals.py [--sleep 2]
Tip: free tiers are rate limited; --sleep spaces out tickets.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from support_agent import tools  # noqa: E402
from support_agent.config import get_fast_llm, get_main_llm  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402
from support_agent.main import run_ticket  # noqa: E402


def run(fast, main, sleep=0.0):
    tickets = json.loads((ROOT / "evals" / "tickets.json").read_text())
    graph = build_graph(fast, main, MemorySaver())
    passed = 0
    for t in tickets:
        tools.reset()
        for oid, amt in t.get("preset_refunded", {}).items():
            tools.ORDERS[oid]["refunded"] = amt
        out = run_ticket(graph, t["message"], t["email"], ticket_id=t["id"], approver=lambda p: "approve")
        ok = out["resolution"] == t["expect_resolution"] and (out["_approvals"] > 0) == t["expect_approval"]
        if "expect_refund_amount" in t:
            ok = ok and out.get("tool_result", {}).get("refunded") == t["expect_refund_amount"]
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} {t['id']}: got={out['resolution']} approvals={out['_approvals']} "
              f"expected={t['expect_resolution']}/{t['expect_approval']}")
        time.sleep(sleep)
    print(f"\n{passed}/{len(tickets)} passed")
    return passed, len(tickets)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=2.0)
    run(get_fast_llm(), get_main_llm(), ap.parse_args().sleep)
