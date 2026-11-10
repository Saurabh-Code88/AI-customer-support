# Customer Support Resolution Agent (LangGraph + Gemini + Groq)

Gemini (free tier) handles the `decide` node; Groq (free tier) handles `classify` and `respond`.
Business rules live in `policy.py` as plain code, so the LLM cannot talk its way past them.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GOOGLE_API_KEY and GROQ_API_KEY
```

## Run
```bash
# offline logic tests (no API keys needed)
PYTHONPATH=src pytest -q

# one ticket against real models
PYTHONPATH=src python -m support_agent.main --email a@example.com "Please refund order A1001"

# eval set against real models (free tiers are rate limited)
python evals/run_evals.py --sleep 3
```

## Web UI + API
```bash
pip install -r requirements.txt          # adds fastapi + uvicorn
# Windows PowerShell:  $env:PYTHONPATH="src"      Mac/Linux: export PYTHONPATH=src
python -m uvicorn support_agent.api:app --reload
```
Open http://127.0.0.1:8000 . Endpoints: `POST /tickets`, `GET /tickets/{id}`, `POST /tickets/{id}/approval`,
`GET /demo/orders`, `POST /demo/reset`, `GET /health`. Interactive API docs at `/docs`.

## Graph
classify -> retrieve_context -> decide -> policy_check -> (human_approval) -> execute_tool -> respond -> log
`decide` can also route to `respond` (clarify) or `escalate`. Tool failures retry via `decide` up to MAX_LOOPS.

## Next steps
Swap `tools.py` for real APIs, `retrieval.py` for a vector store, `MemorySaver` for SQLite/Postgres checkpointer.
