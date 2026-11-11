"""Model setup. Gemini = reasoning (decide node). Groq = fast steps (classify, respond)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root (…/support-agent/support-agent), not the process cwd.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# LangGraph reads this when its checkpoint serializer is imported.
LANGGRAPH_STRICT_MSGPACK = os.getenv("LANGGRAPH_STRICT_MSGPACK", "true").lower()
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", LANGGRAPH_STRICT_MSGPACK)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAIN_PROVIDER = os.getenv("MAIN_PROVIDER", "gemini").lower()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "./support_agent.db")
DATABASE_CONNECT_TIMEOUT = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "3"))
PORT = int(os.getenv("PORT", "8000"))
WEB_CONCURRENCY = int(os.getenv("WEB_CONCURRENCY", "1"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Business rules (enforced in plain code, never by the LLM)
REFUND_WINDOW_DAYS = int(os.getenv("REFUND_WINDOW_DAYS", "30"))
REFUND_AUTO_LIMIT = float(os.getenv("REFUND_AUTO_LIMIT", "2000"))
MAX_LOOPS = int(os.getenv("MAX_LOOPS", "2"))


def get_fast_llm():
    from langchain_groq import ChatGroq

    return ChatGroq(model=GROQ_MODEL, temperature=0)


def get_main_llm():
    if MAIN_PROVIDER == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=GROQ_MODEL, temperature=0)
    if MAIN_PROVIDER != "gemini":
        raise ValueError("MAIN_PROVIDER must be 'gemini' or 'groq'")
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)
