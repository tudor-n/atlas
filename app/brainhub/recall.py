"""Recall: instant FTS hits, a single-round cited answer, and an agentic 'dig deeper'."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

from .models import Hit

NOTHING = "Nothing on that."
MAX_HIT_CHARS = 1500

_STOPWORDS = {
    "what", "did", "i", "say", "about", "the", "a", "an", "of", "on", "to", "is", "was", "for",
    "in", "my", "me", "we", "it", "and", "or", "when", "where", "how", "why", "which", "who",
}


def build_fts_query(question: str) -> str | None:
    """Quote every token so FTS operators are plain words; None if nothing is left."""
    tokens = [t for t in re.findall(r"\w+", question.lower()) if len(t) >= 2 and t not in _STOPWORDS]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search_hits(store, question: str, limit: int) -> list[Hit]:
    fq = build_fts_query(question)
    if fq is None:
        return []
    return store.search(fq, limit)


def build_quick_prompt(question: str, hits: list[Hit]) -> str:
    lines = []
    for h in hits:
        text = h.text if len(h.text) <= MAX_HIT_CHARS else h.text[:MAX_HIT_CHARS] + "…"
        lines.append(f"[{h.ref}] ({h.date}) {text}")
    return "Sources:\n" + "\n\n".join(lines) + f"\n\nQuestion: {question}"


async def quick_answer(runner, question: str, hits: list[Hit], settings: dict, prompts_dir: Path,
                       cwd: Path) -> AsyncIterator[str]:
    if not hits:
        yield NOTHING
        return
    async for chunk in runner.stream_text(
            build_quick_prompt(question, hits), cwd=cwd, model=settings["recall_model"],
            system_prompt_file=prompts_dir / "recall.md", allowed_tools=[], max_turns=1,
            timeout_s=settings["recall_timeout_s"]):
        yield chunk


async def deeper_answer(runner, question: str, settings: dict, prompts_dir: Path,
                        brain: Path) -> AsyncIterator[str]:
    async for chunk in runner.stream_text(
            question, cwd=brain, model=settings["deeper_model"],
            system_prompt_file=prompts_dir / "deeper.md", allowed_tools=["Read", "Glob", "Grep"],
            max_turns=15, timeout_s=settings["deeper_timeout_s"]):
        yield chunk
