"""Model setup. Gemini = reasoning (decide node). Groq = fast steps (classify, respond)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root (…/support-agent/support-agent), not the process cwd.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Business rules (enforced in plain code, never by the LLM)
REFUND_WINDOW_DAYS = 30
REFUND_AUTO_LIMIT = 2000  # refunds above this need human approval
MAX_LOOPS = 2             # max tool-failure retries before escalating

AUDIT_PATH = os.getenv("AUDIT_PATH", "audit.jsonl")


def get_fast_llm():
    from langchain_groq import ChatGroq

    return ChatGroq(model=GROQ_MODEL, temperature=0)

MAIN_PROVIDER = os.getenv("MAIN_PROVIDER", "gemini")  

def get_main_llm():
       if MAIN_PROVIDER == "groq":
           from langchain_groq import ChatGroq

           return ChatGroq(model=GROQ_MODEL, temperature=0)
       from langchain_google_genai import ChatGoogleGenerativeAI

       return ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)