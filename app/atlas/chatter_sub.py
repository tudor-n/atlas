"""Chatter's use of the owner's subscription (chatter spec §6), only where `claude -p`'s slowness
doesn't show: nothing here is ever on the path of a spoken line.

- A, fresh quips: once a day while the owner is away (or in quiet hours), a batch of new quips and
  info wordings for the pools, written to `brain/_voice/generated.md` (replaced per batch) and
  committed. They are templates: placeholders filled from real values, as the owner's lines are.
- B, briefings worded while the owner is away: a welcome back from a fact sheet, spoken on return
  only if its sheet still equals the current one and it passes `honest()` (V16 amended, §7).
- C, ATLAS's week: once a week, a short self-review from stats, with up to two suggestions the owner
  can ✓ into a task in ATLAS's project.

All of it is behind `voice_subscription` and its own switch, skipped above `voice_sub_max_pct` of
the 5-hour window (Try it now skips that), boxed in like filing (no tools at all), and reported to
the usage meter by the runner. No agent text goes into any prompt: names from the map, counts."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import voice_lines as vl
from .lines import POOLS as LINE_POOLS
from .lines import generated_path, load_all
from .quotes import CHATTER_POOLS, PLACEHOLDERS, load_quotes
from .voice import in_quiet_hours

log = logging.getLogger(__name__)
t = asyncio.to_thread

QUIPS_EVERY = timedelta(hours=20)
REVIEW_EVERY = timedelta(days=7)
BRIEFINGS_A_DAY = 6
TIMEOUT_S = 180
MAX_TURNS = 2
RUNS_KEPT = timedelta(days=7)

QUIP_POOLS = ("agent_finished", "agent_waiting", "reminder", "reply_tasks", "reply_found", *CHATTER_POOLS)
WORDING_POOLS = ("agent_waiting.idle", "agent_waiting.permission", "agent_waiting.question", "agent_finished",
                 "reminder", "due_today.repeat_one", "briefing.back", "self.musing")

_LINE = {"type": "array", "items": {"type": "string", "maxLength": 140}, "maxItems": 12}
QUIPS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["quips", "lines"], "properties": {
    "quips": {"type": "object", "additionalProperties": _LINE},
    "lines": {"type": "object", "additionalProperties": _LINE}}}
BRIEFING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
                   "properties": {"text": {"type": "string", "maxLength": 300}}}
WEEK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "paragraphs", "suggestions"],
               "properties": {"title": {"type": "string", "maxLength": 80},
                              "paragraphs": {"type": "array", "items": {"type": "string", "maxLength": 700}, "maxItems": 3},
                              "suggestions": {"type": "array", "items": {"type": "string", "maxLength": 140},
                                              "maxItems": 2}}}

_WORD_NUMBERS = {w: i for i, w in enumerate(vl._ONES)} | {w: i * 10 for i, w in enumerate(vl._TENS) if w}
_BAD = re.compile(r"[{}:;–—()\[\]\"]| - ")
# Words a briefing's sentence may start with besides a name from its sheet. Anything else that's
# capitalised is treated as a name, so an invented one can't hide at the start of a sentence.
_STARTERS = set("""a all also and another anyway apparently as at back both busy but don't everything go good great
heads in it it's its just luckily meanwhile my nice no none not nothing now oh on only plus quick quiet right sadly
shocking so somebody someone something still that that's the then there there's these they this those today
tonight welcome well what while yes you you're your""".split()) | set(_WORD_NUMBERS)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)(\w+)")


def wording_ok(line: str, limit: int = 120) -> bool:
    """Phase 4's wording rules for a generated line: short, plain, ending a sentence."""
    body = line[2:] if line.startswith("~ ") else line
    stripped = re.sub(r"\{\w+\}", "", body)
    return (0 < len(body) <= limit and not _BAD.search(stripped) and body.rstrip().endswith((".", "?", "!", "}"))
            and stripped.count("{") == 0)


def honest(text: str, sheet: dict) -> bool:
    """May a model-worded briefing be spoken? Every number and every name in it must be in its
    fact sheet; at most three sentences and 300 characters; no braces, colons, dashes or brackets."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300 or _BAD.search(text):
        return False
    if len(re.findall(r"[.!?](\s|$)", text.strip())) > 3:
        return False
    names = " ".join([*sheet.get("waiting", []), *sheet.get("finished", []), *sheet.get("due_today", [])]).lower()
    counts = {len(sheet.get("waiting", [])), len(sheet.get("finished", [])), sheet.get("due_count", 0)}
    if re.search(r"\d", text):  # numbers are spoken as words: a digit means the wording went astray
        return False
    for w in re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower()):
        if w in _WORD_NUMBERS or "-" in w and all(p in _WORD_NUMBERS for p in w.split("-")):
            n = sum(_WORD_NUMBERS[p] for p in w.split("-"))
            if n not in counts and n != 1:  # "one" reads as a word as often as a number
                return False
    starts = {m.group(1) for m in _SENTENCE_START.finditer(text)}
    for word in re.findall(r"\b[A-Z][\w']*", text):
        if word.lower() in names or word in ("I", "I'm", "I'll", "I've"):
            continue
        if word in starts and word.lower() in _STARTERS:
            continue
        return False  # a name the sheet doesn't have (or a sentence that starts oddly: the template plays)
    return True


class Sub:
    def __init__(self, *, runner, settings: Callable[[], dict], paths, usage: Callable[[], dict | None],
                 presence, chatter, voice, commit: Callable[[str], None], reviewer, store,
                 now: Callable[[], datetime] = datetime.now):
        self.runner, self._settings, self.paths, self._usage = runner, settings, paths, usage
        self.presence, self.chatter, self.voice, self._commit = presence, chatter, voice, commit
        self.reviewer, self.store, self._now = reviewer, store, now
        self.running: str | None = None
        self.last_error: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._briefing: dict | None = None  # {"sheet", "text"} worded while the owner was away
        self._dirty = True

    # -- state ------------------------------------------------------------------------------------
    @property
    def _st(self) -> dict:
        return self.voice.chatter_state().setdefault("sub", {})

    def _note_run(self, name: str) -> None:
        now = self._now()
        with self.voice._lock:
            runs = [r for r in self._st.get("runs", []) if (_parse(r) or now) > now - RUNS_KEPT]
            self._st["runs"] = runs + [now.isoformat(timespec="seconds")]
            self._st.setdefault("last", {})[name] = now.isoformat(timespec="seconds")
            self.voice.save_state()

    def view(self) -> dict:
        now = self._now()
        runs = [r for r in self._st.get("runs", []) if (_parse(r) or now) > now - RUNS_KEPT]
        return {"runs_week": len(runs), "last": dict(self._st.get("last", {})), "running": self.running,
                "last_error": self.last_error, "on": self._settings()["voice_subscription"]}

    def enabled(self, name: str, manual: bool = False) -> bool:
        s = self._settings()
        if not s["voice_subscription"] or not s.get(f"voice_sub_{name}"):
            return False
        if manual:
            return True
        five = (self._usage() or {}).get("five_hour") or {}
        return not (five.get("used") is not None and five["used"] >= s["voice_sub_max_pct"])

    def _away_or_quiet(self) -> bool:
        s = self._settings()
        return ((self.presence is not None and self.presence.state() == "away")
                or in_quiet_hours(self._now(), s["quiet_hours"]))

    def _due(self, name: str, every: timedelta, first: bool) -> bool:
        last = _parse(self._st.get("last", {}).get(name))
        if last is None:
            if not first:  # the first week's review waits a week from now
                with self.voice._lock:
                    self._st.setdefault("last", {})[name] = self._now().isoformat(timespec="seconds")
                    self.voice.save_state()
            return first
        return self._now() - last >= every

    # -- the tick (on the loop, every 30 s with the voice's) ---------------------------------------
    async def tick(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self.running:
            return
        try:
            if self.enabled("briefings") and self.presence is not None and self.presence.state() == "away":
                await self._maybe_brief()
            if self.enabled("quips") and self._away_or_quiet() and self._due("quips", QUIPS_EVERY, True):
                await self.quip_batch()
            if self.enabled("review") and self._away_or_quiet() and self._due("review", REVIEW_EVERY, False):
                await self.weekly_review()
        except Exception as e:  # never the hub's problem
            self.last_error = str(e)[:300]
            log.exception("chatter: a subscription run failed")

    def run_now(self, name: str) -> bool:
        """Try it now (from a thread): start pattern `quips`, `briefings` or `review` at once."""
        if name not in ("quips", "briefings", "review") or self.loop is None or self.running:
            return False
        if not self.enabled(name, manual=True):
            return False
        coro = {"quips": self.quip_batch, "briefings": self._brief_now, "review": self.weekly_review}[name]()
        asyncio.run_coroutine_threadsafe(self._guard(coro), self.loop)
        return True

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception as e:
            self.last_error = str(e)[:300]
            log.exception("chatter: a subscription run failed")

    async def _run(self, name: str, prompt: str, system: str, schema: dict) -> dict:
        self.running = name
        try:
            cwd = self.paths.data / "chatter"
            cwd.mkdir(parents=True, exist_ok=True)
            out = await self.runner.run_json(prompt, cwd=cwd, model=self._settings()["recall_model"],
                                             system_prompt_file=self.paths.prompts / system, allowed_tools=[],
                                             json_schema=schema, max_turns=MAX_TURNS, timeout_s=TIMEOUT_S)
            self.last_error = None
            return out
        finally:
            self.running = None
            self._note_run(name)

    # -- A: fresh quips ----------------------------------------------------------------------------
    async def quip_batch(self) -> int:
        brain = self.paths.brain
        quotes, _ = await t(load_quotes, brain)
        wordings, _, _ = await t(load_all, brain)
        facts = await t(self._week_facts)
        pools = {"quips": {p: {"placeholders": sorted(PLACEHOLDERS), "examples": quotes.get(p, [])[:4]}
                           for p in QUIP_POOLS},
                 "lines": {p: {"required": sorted(LINE_POOLS[p][0]), "optional": sorted(LINE_POOLS[p][1]),
                               "examples": wordings.get(p, [])[:3]} for p in WORDING_POOLS}}
        prompt = ("Write up to 6 new lines for each pool.\n" +
                  json.dumps({"pools": pools, "week": facts}, ensure_ascii=False, indent=1))
        raw = await self._run("quips", prompt, "quips.md", QUIPS_SCHEMA)
        text, n = render_generated(raw, quotes, wordings, self._now())
        if n:
            await t(_write, generated_path(brain), text)
            await t(self._commit, "chatter: fresh quips")
        return n

    def _week_facts(self) -> dict:
        now = self._now()
        days = [(now - timedelta(days=i)).date().isoformat() for i in range(7)]
        filed = sum(len(self.store.list_entries(day=d)) for d in days)
        items = [i for i in self.store.list_items(status=["open", "snoozed", "done"])
                 if (_parse(i.created) or now) > now - timedelta(days=7)]
        by_project: dict[str, int] = {}
        for i in items:
            if i.project:
                by_project[i.project] = by_project.get(i.project, 0) + 1
        top = sorted(by_project, key=lambda k: -by_project[k])[:3]
        names = {}
        try:
            from .brainmap import load_map
            m = load_map(self.paths.brain)
            names = {p["slug"]: p.get("name") or p["slug"] for p in (m.projects if m else [])}
        except Exception:
            pass
        return {"notes_filed": filed, "busiest_projects": [names.get(s, s) for s in top],
                "time_of_week": now.strftime("%A")}

    # -- B: briefings worded while away ------------------------------------------------------------
    def facts_changed(self, sheet: dict) -> None:
        self._dirty = True

    def briefing_for(self, sheet: dict) -> str | None:
        b = self._briefing
        if b and b["sheet"] == sheet and honest(b["text"], sheet):
            return b["text"]
        return None

    async def _maybe_brief(self) -> None:
        sheet = await t(self.chatter.fact_sheet, self.presence.away_since())
        if not (sheet["waiting"] or sheet["finished"] or sheet["due_today"]):
            return
        if self._briefing and self._briefing["sheet"] == sheet:
            return
        b = self.voice.chatter_state().get("sub_day") or {}
        today = self._now().date().isoformat()
        if b.get("day") != today:
            b = {"day": today, "briefings": 0}
        if b["briefings"] >= BRIEFINGS_A_DAY:
            return
        b["briefings"] += 1
        with self.voice._lock:
            self.voice.chatter_state()["sub_day"] = b
            self.voice.save_state()
        await self._brief(sheet)

    async def _brief_now(self) -> None:
        since = self.presence.away_since() if self.presence is not None else None
        sheet = await t(self.chatter.fact_sheet, since or self._now() - timedelta(hours=1))
        await self._brief(sheet)
        text = self.briefing_for(sheet)
        if text:
            await t(self.voice.chat, "briefing", "briefing.back", {}, "", force=True, why="try it now (worded)",
                    text=f"Welcome back. {text}")

    async def _brief(self, sheet: dict) -> None:
        raw = await self._run("briefings", json.dumps(sheet, ensure_ascii=False), "briefing.md", BRIEFING_SCHEMA)
        text = " ".join(str(raw.get("text", "")).split())
        self._briefing = {"sheet": sheet, "text": text} if honest(text, sheet) else None
        if self._briefing is None:
            log.info("chatter: a worded briefing failed the check: %r", text[:200])

    # -- C: ATLAS's week ---------------------------------------------------------------------------
    def week_path(self) -> Path:
        return self.paths.data / "chatter" / "week.json"

    def week(self) -> dict | None:
        try:
            data = json.loads(self.week_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    async def weekly_review(self) -> None:
        stats = await t(self._review_stats)
        raw = await self._run("review", json.dumps(stats, ensure_ascii=False), "week.md", WEEK_SCHEMA)
        now = self._now()
        week = {"title": " ".join(str(raw.get("title", "ATLAS's week")).split())[:80],
                "written_at": now.isoformat(timespec="minutes"),
                "paragraphs": [" ".join(str(p).split())[:700] for p in raw.get("paragraphs", [])][:3],
                "suggestions": [{"id": "w-" + secrets.token_hex(3), "text": " ".join(str(x).split())[:140],
                                 "status": "open"} for x in raw.get("suggestions", [])][:2]}
        await t(_write, self.week_path(), json.dumps(week, indent=1))
        await t(self.voice.chat, "self", "self.week", {}, "I wrote up my week. It's on the Atlas screen.",
                why="ATLAS's week is written")

    def _review_stats(self) -> dict:
        now = self._now()
        days = [(now - timedelta(days=i)).date().isoformat() for i in range(7)]
        st = self.voice.chatter_state()
        tally = st.get("tally", {})
        sums: dict[str, int] = {}
        for d in days:
            for k, n in (tally.get(d) or {}).items():
                sums[k] = sums.get(k, 0) + n
        shh = {e: len(v) for e, v in (st.get("shh") or {}).items() if v}
        return {"notes_filed": sum(len(self.store.list_entries(day=d)) for d in days),
                "corrections": sums.get("correction", 0), "retries": sums.get("retry", 0),
                "guess_overruled": sums.get("flip_ask", 0) + sums.get("flip_note", 0),
                "silenced_line_kinds": shh, "subscription_runs": self.view()["runs_week"],
                "chattiness": self._settings()["voice_chattiness"]}

    def decide(self, sid: str, confirm: bool) -> dict:
        """✓ or ✕ on one of the week's suggestions. ✓ makes it an open task in ATLAS's project."""
        week = self.week()
        sug = next((x for x in (week or {}).get("suggestions", []) if x.get("id") == sid), None)
        if sug is None or sug.get("status") != "open":
            raise ValueError("unknown or already handled suggestion")
        if confirm:
            self.reviewer.add_task(sug["text"], project=self.chatter.atlas_project(), area="projects",
                                   source=f"atlas-week-{week['written_at'][:10]}")
        sug["status"] = "confirmed" if confirm else "dismissed"
        _write(self.week_path(), json.dumps(week, indent=1))
        return week


def render_generated(raw: dict, quotes: dict, wordings: dict, now: datetime) -> tuple[str, int]:
    """generated.md's text from a batch, keeping only lines that pass their pool's rules and the
    wording rules and aren't already there. (text, how many lines)."""
    from .lines import parse as parse_lines
    from .quotes import parse as parse_quips
    out = [f"# Written by ATLAS's overnight batch on {now.date().isoformat()} (chatter pattern A).",
           "# The next batch replaces this file. Delete any line you don't like; the hub reads it every time.", ""]
    n = 0
    for kind, allowed, existing in (("quips", QUIP_POOLS, quotes), ("lines", WORDING_POOLS, wordings)):
        got = raw.get(kind) if isinstance(raw, dict) else None
        for pool in allowed:
            lines = []
            for line in (got or {}).get(pool, []) if isinstance(got, dict) else []:
                line = " ".join(str(line).split())
                if not wording_ok(line) or line in existing.get(pool, []) or line in lines:
                    continue
                check = parse_quips(f"## {pool}\n{line}\n") if kind == "quips" else parse_lines(f"## {pool}\n{line}\n")
                if check[-1] or not check[0][pool]:
                    continue
                lines.append(line)
            if lines:
                out += [f"## {kind} {pool}", *lines[:8], ""]
                n += len(lines[:8])
    return "\n".join(out), n


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)


def _parse(s) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if isinstance(s, str) and s else None
    except ValueError:
        return None
