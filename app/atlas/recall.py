"""Recall: instant FTS hits, a single-round cited answer, and an agentic 'dig deeper'."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path

from .models import Hit

NOTHING = "Nothing on that."
MAX_HIT_CHARS = 1500

_STOPWORDS = {
    "what", "did", "i", "say", "about", "the", "a", "an", "of", "on", "to", "is", "was", "for",
    "in", "my", "me", "we", "it", "and", "or", "when", "where", "how", "why", "which", "who",
}


def _tokens(question: str) -> list[str]:
    return [t for t in re.findall(r"\w+", question.lower()) if len(t) >= 2 and t not in _STOPWORDS]


def build_fts_query(question: str) -> str | None:
    """Quote every token so FTS operators are plain words; None if nothing is left."""
    tokens = _tokens(question)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search_hits(store, question: str, limit: int) -> list[Hit]:
    fq = build_fts_query(question)
    if fq is None:
        return []
    return store.search(fq, limit)


def _fold(text: str) -> tuple[str, list[int]]:
    """Lowercased with diacritics dropped (as the FTS tokenizer matches), plus a map from each
    folded character back to its index in `text`."""
    chars: list[str] = []
    index: list[int] = []
    for i, c in enumerate(text):
        for f in unicodedata.normalize("NFKD", c):
            if not unicodedata.combining(f):
                for g in f.lower():
                    chars.append(g)
                    index.append(i)
    return "".join(chars), index


def _window(text: str, terms: list[str], size: int = MAX_HIT_CHARS) -> str:
    """At most `size` characters of `text`, centred on the first place a query term occurs
    (the start when none does); a cut end is marked with an ellipsis."""
    if len(text) <= size:
        return text
    folded, index = _fold(text)
    first = None
    for term in terms:
        m = re.search(rf"\b{re.escape(_fold(term)[0])}\b", folded)
        if m and (first is None or index[m.start()] < first):
            first = index[m.start()]
    start = 0 if first is None else max(0, min(first - size // 2, len(text) - size))
    end = start + size
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def build_quick_prompt(question: str, hits: list[Hit]) -> str:
    """One labelled block per hit: raw entries are the owner's own words, notes were written by
    the filing agent. Dates appear only when known."""
    terms = _tokens(question)
    lines = []
    for h in hits:
        label = "owner's words" if h.kind == "entry" else "agent-written note"
        if h.date:
            label += f", {h.date}"
        lines.append(f"[{h.ref}] ({label}) {_window(h.text, terms)}")
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
