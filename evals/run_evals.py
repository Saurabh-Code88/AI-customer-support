"""Run the ticket eval set against the real models (Gemini + Groq).
Usage: python evals/run_evals.py [--sleep 2]
Tip: free tiers are rate limited; --sleep spaces out tickets.
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from support_agent import tools  # noqa: E402
from support_agent.config import get_fast_llm, get_main_llm  # noqa: E402
from support_agent.database import checkpointer_context, run_migrations  # noqa: E402
from support_agent.graph import build_graph  # noqa: E402
from support_agent.main import run_ticket  # noqa: E402
from support_agent.seed import seed_orders  # noqa: E402


def run(fast, main, sleep=0.0):
    tickets = json.loads((ROOT / "evals" / "tickets.json").read_text())
    run_migrations()
    seed_orders()
    passed = 0
    with checkpointer_context() as checkpointer:
        graph = build_graph(fast, main, checkpointer)
        for t in tickets:
            tools.reset()
            for oid, amt in t.get("preset_refunded", {}).items():
                tools.issue_refund(
                    order_id=oid,
                    amount=amt,
                    ticket_id=f"eval-setup-{t['id']}-{oid}",
                )
            ticket_id = f"eval-{uuid.uuid4().hex[:8]}-{t['id']}"
            out = run_ticket(
                graph,
                t["message"],
                t["email"],
                ticket_id=ticket_id,
                approver=lambda p: "approve",
            )
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
