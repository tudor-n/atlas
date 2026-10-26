"""Chatter's use of the owner's subscription (chatter spec §6), only where `claude -p`'s slowness
doesn't show: nothing here is ever on the path of a spoken line.

- A, fresh quips: once a day while the owner is away (or in quiet hours), a batch of new quips for
  the pools, written to `brain/_voice/generated.md` (replaced per batch) and committed. Quips only:
  the information a line carries always comes from code (review, CH22).
- B, the welcome back's joke, written while the owner is away from counts only: the facts of the
  briefing are code's; the model adds the last sentence, only if it's still for the same counts.
- C, ATLAS's week: once a week, a short self-review from stats, with up to two suggestions the owner
  can ✓ into a task in ATLAS's project.

All of it is behind `voice_subscription` and its own switch, skipped above `voice_sub_max_pct` of
the 5-hour window (Try it now skips that), boxed in like filing (no tools at all), and reported to
the usage meter by the runner. No agent text goes into any prompt: names from the map, counts. Every
line a model writes must pass `fact_free`: it can be a joke, never a claim."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import voice_lines as vl
from .lines import generated_path
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

_LINE = {"type": "array", "items": {"type": "string", "maxLength": 140}, "maxItems": 12}
QUIPS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["quips"], "properties": {
    "quips": {"type": "object", "additionalProperties": _LINE}}}
BRIEFING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["quip"],
                   "properties": {"quip": {"type": "string", "maxLength": 120}}}
WEEK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "paragraphs", "suggestions"],
               "properties": {"title": {"type": "string", "maxLength": 80},
                              "paragraphs": {"type": "array", "items": {"type": "string", "maxLength": 700}, "maxItems": 3},
                              "suggestions": {"type": "array", "items": {"type": "string", "maxLength": 140},
                                              "maxItems": 2}}}

_BAD = re.compile(r"[{}:;/&$%#*<>@|\\\u2013\u2014()\[\]\"]| - |\d")
# Words that state a quantity, a time or a happening: a joke has no need of them, a claim does.
_CLAIMS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand
thousands million millions billion couple dozen dozens half both twice thrice several few first second third
fourth fifth last next am pm noon midnight o'clock tomorrow tonight yesterday today monday tuesday wednesday
thursday friday saturday sunday january february march april may june july august september october november
december week weeks month months minute minutes hour hours deadline due overdue called emailed texted phoned
messaged crashed deleted failed failing broke broken error errors deployed pushed merged password bank urgent
emergency fire police doctor hospital nothing nobody none""".split())
_OPENERS = set("""a all also and anyway as at back but don't go good great just look maybe my nice no not now oh
right so still that that's the then there there's this time try well what while yes you you're your i i'm i'll
i've it it's""".split())


def wording_ok(line: str, limit: int = 120) -> bool:
    """Phase 4's wording rules for a generated line: short, plain, ending a sentence."""
    stripped = re.sub(r"\{\w+\}", "", line)
    return (0 < len(line) <= limit and not _BAD.search(stripped) and line.rstrip().endswith((".", "?", "!", "}"))
            and stripped.count("{") == 0)


def fact_free(text: str, limit: int = 120, *, strict: bool = False) -> bool:
    """May a line a model wrote be spoken? It may joke but never claim: phase 4's wording rules, at
    most two sentences, no digit, no number, time, day or happening word, no capitalised word inside
    a sentence (a name). `strict` (B's joke, said inside the briefing) also allows only common
    openers at a sentence's start, so no name can lead one. The facts a line carries are code's (CH22)."""
    if not isinstance(text, str) or not wording_ok(text, limit):
        return False
    body = re.sub(r"\{\w+\}", "", text)
    if len([x for x in re.split(r"[.!?]+", body) if x.strip()]) > 2:
        return False
    words = re.findall(r"[A-Za-z']+", body)
    if any(w.lower().strip("'") in _CLAIMS for w in words):
        return False
    starts = {m.group(1) for m in re.finditer(r"(?:^|[.!?]\s+)([A-Za-z']+)", body.strip())}
    return all(not w[0].isupper() or (w in starts and (not strict or w.lower() in _OPENERS
                                                        or w.lower().endswith("ly"))) for w in words)


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
        self._briefing: dict | None = None  # {"sheet", "quip"} written while the owner was away
        self._decide = threading.Lock()  # ✓/✕ on the week: one at a time, or a double click makes two tasks

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
        """Every voice tick, as its own task (never awaited by the reminder tick: a run takes minutes)."""
        self.loop = asyncio.get_running_loop()
        if self.running:
            return
        try:
            if self.enabled("briefings") and self.presence is not None and self.presence.state() == "away":
                await self._maybe_brief()
            if self.enabled("quips") and self._away_or_quiet() and await t(self._due, "quips", QUIPS_EVERY, True):
                await self.quip_batch()
            if self.enabled("review") and self._away_or_quiet() and await t(self._due, "review", REVIEW_EVERY, False):
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
        coro = {"quips": self.quip_batch, "briefings": self._quip_now, "review": self.weekly_review}[name]()
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
            await t(self._note_run, name)  # the Announcer's lock: never taken on the loop

    # -- A: fresh quips ----------------------------------------------------------------------------
    async def quip_batch(self) -> int:
        brain = self.paths.brain
        quotes, _ = await t(load_quotes, brain)
        facts = await t(self._week_facts)
        pools = {p: {"placeholders": sorted(PLACEHOLDERS), "examples": quotes.get(p, [])[:4]} for p in QUIP_POOLS}
        prompt = ("Write up to 6 new quips for each pool.\n" +
                  json.dumps({"pools": pools, "week": facts}, ensure_ascii=False, indent=1))
        raw = await self._run("quips", prompt, "quips.md", QUIPS_SCHEMA)
        text, n = render_generated(raw, quotes, self._now())
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

    # -- B: the welcome back's joke, written while away ------------------------------------------
    def facts_changed(self, sheet: dict) -> None:
        pass  # the tick notices: it writes again when the counts differ from the last joke's

    def quip_for(self, sheet: dict) -> str | None:
        """The joke written for exactly these counts, if it still passes the check."""
        b = self._briefing
        if b and b["sheet"] == sheet and fact_free(b["quip"], 90, strict=True):
            return b["quip"]
        return None

    async def _maybe_brief(self) -> None:
        sheet = await t(self.chatter.fact_sheet, self.presence.away_since())
        if not any(sheet.values()) or (self._briefing and self._briefing["sheet"] == sheet):
            return
        if not await t(self._count_briefing):
            return
        await self._brief(sheet)

    def _count_briefing(self) -> bool:
        with self.voice._lock:
            st = self.voice.chatter_state()
            today = self._now().date().isoformat()
            b = st.get("sub_day") if isinstance(st.get("sub_day"), dict) and st["sub_day"].get("day") == today \
                else {"day": today, "briefings": 0}
            if b["briefings"] >= BRIEFINGS_A_DAY:
                return False
            b["briefings"] += 1
            st["sub_day"] = b
            self.voice.save_state()
            return True

    async def _quip_now(self) -> None:
        since = self.presence.away_since() if self.presence is not None else None
        sheet = await t(self.chatter.fact_sheet, since or self._now() - timedelta(hours=1))
        await self._brief(sheet)
        quip = self.quip_for(sheet)
        if quip:
            await t(self.voice.chat, "briefing", "briefing.back", {}, "", force=True, why="try it now (the joke is B's)",
                    text=f"Welcome back. {quip}")

    async def _brief(self, sheet: dict) -> None:
        raw = await self._run("briefings", json.dumps(sheet), "briefing.md", BRIEFING_SCHEMA)
        quip = " ".join(str(raw.get("quip", "")).split())
        self._briefing = {"sheet": sheet, "quip": quip} if fact_free(quip, 90, strict=True) else None
        if self._briefing is None:
            log.info("chatter: the welcome back's joke failed the check: %r", quip[:200])

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
        """✓ or ✕ on one of the week's suggestions. ✓ makes it an open task in ATLAS's project (a
        project and an area from the map, or none). One at a time: a double click is one task."""
        with self._decide:
            week = self.week()
            sug = next((x for x in (week or {}).get("suggestions", []) if x.get("id") == sid), None)
            if sug is None or sug.get("status") != "open":
                raise ValueError("unknown or already handled suggestion")
            if confirm:
                project, area = self._home()
                self.reviewer.add_task(sug["text"], project=project, area=area,
                                       source=f"atlas-week-{week['written_at'][:10]}")
            sug["status"] = "confirmed" if confirm else "dismissed"
            _write(self.week_path(), json.dumps(week, indent=1))
            return week

    def _home(self) -> tuple[str | None, str]:
        """Where a week's task goes: ATLAS's project if the map has it, and an area the map has."""
        from .brainmap import load_map
        m = load_map(self.paths.brain)
        projects = {p.get("slug") for p in (m.projects if m else [])}
        areas = [a.get("slug") for a in (m.areas if m else [])]
        slug = self.chatter.atlas_project()
        area = "projects" if "projects" in areas or not areas else areas[0]
        return (slug if slug in projects else None), area


def render_generated(raw: dict, quotes: dict, now: datetime) -> tuple[str, int]:
    """generated.md's text from a batch: quips that pass their pool's rules, `fact_free` and aren't
    already there. (text, how many)."""
    from .quotes import parse as parse_quips
    out = [f"# Written by ATLAS's overnight batch on {now.date().isoformat()} (chatter pattern A).",
           "# The next batch replaces this file. Delete any line you don't like; the hub reads it every time.", ""]
    n = 0
    got = raw.get("quips") if isinstance(raw, dict) else None
    for pool in QUIP_POOLS:
        lines = []
        for line in (got or {}).get(pool, []) if isinstance(got, dict) else []:
            line = " ".join(str(line).split())
            if not fact_free(line) or line in quotes.get(pool, []) or line in lines:
                continue
            check = parse_quips(f"## {pool}\n{line}\n")
            if check[-1] or not check[0][pool]:
                continue
            lines.append(line)
        if lines:
            out += [f"## quips {pool}", *lines[:8], ""]
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
