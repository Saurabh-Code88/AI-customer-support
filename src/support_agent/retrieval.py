"""Stub RAG: section lookup by intent. Swap for a vector store (Chroma, pgvector) later."""
from pathlib import Path

_TEXT = (Path(__file__).parent / "policies.md").read_text()
_SECTIONS = {}
for block in _TEXT.split("## ")[1:]:
    title, _, body = block.partition("\n")
    _SECTIONS[title.strip()] = body.strip()


def retrieve_policies(intent: str) -> list[str]:
    return [_SECTIONS[intent]] if intent in _SECTIONS else [_SECTIONS["other"]]
