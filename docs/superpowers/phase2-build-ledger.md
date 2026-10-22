# Phase 2 build ledger (copied from the git-ignored SDD workspace at the end of the build, 2026-10-21)

Spec: docs/superpowers/specs/2026-10-21-brainhub-phase2-design.md (parent: 2026-10-20-brainhub-design.md)
Branch: phase-2 (main checkout D:\BrainHub, not master). Branch base: 305c836 (merge-base with master: 1e41a5f)

## Preflight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 → T2,T3,T7 | settings keys usage_warn_pct, fable_usage_url (T2), agents_end_linger_s, agents_stale_h (T3), arm_url, widget_tiles (T7) | consistent |
| T1 → T10 | conftest autouse LOCALAPPDATA tmp; T10 default_target reads LOCALAPPDATA | consistent |
| T2 → T6 | UsageStore.updated_at() | consistent |
| T2 → T7, T8, T9 | usage view W.resets_at "YYYY-MM-DDTHH:MM", used/last_used/reset_passed/warn | consistent |
| T3 → T4 | AgentTracker(store, paths, settings, now), handle/rows/prune/edit/remove | consistent |
| T3 → T6 | AgentTracker.last_event_at() | consistent |
| T3 → T7 | rows() dict keys status/project_name/part/where/next_time | consistent |
| T5 → T6 | hook_client CLI `event --port P`, `statusline --port P --passthrough-file F` | consistent |
| T6 → T10 | both edit __main__.py subcommand dispatch | sequential, consistent |
| T7 → T9 | /api/widget shape; /widget 404 until T9 then 200 | consistent (T9 updates test) |
| T6,T7 → T8 | /api/integration, /api/open | consistent (T8 after T7) |
| T1 self | tests vs validate rules | consistent |
| T2 self | test alias name test_corrupt_file_starts_empty == test_persists_and_corrupt_file_starts_empty | consistent |
| T3 self | tests vs table §5.1 | consistent |
| T4 self | broadcast test wording vague ("if they do") | Ruling below |
| T5 self | timing assertions vs baseline | consistent |
| T6 self | install uses sys.executable | Ruling below (pythonw) |
| T7 self | consistent |
| T8 self | consistent |
| T9 self | consistent |
| T10 self | consistent |
| T11 self | consistent |

Ruling: T4 broadcast test records by replacing app["broadcast"] with a recorder (routes read app["broadcast"] at call time) before the test client is created — the plan text was non-committal — cost if wrong: test rewritten.
Ruling: T6 installer must write the console interpreter: if sys.executable is pythonw.exe, use the sibling python.exe (hooks need stdin, status line needs stdout; under the desktop supervisor the hub runs as pythonw) — design §4.2 says sys.executable; pythonw would make the status line print nothing — cost if wrong: none (python.exe always exists in a venv).

## Tasks
Task 1: dispatched (base 305c836, implementer accf3fd8ead9ef59f, sonnet)
Task 1: complete (commits 305c836..3dcbfd6, review clean)
Task 1: minor (deferred): config URL regexes use $ (trailing newline passes) — use fullmatch/\Z; T7 must re-validate before opening
Task 1: minor (deferred): URL/limit tests thin (accept-with-path, "https://", space, warn 1/100, invalid stored widget_tiles fallback)
Task 2: dispatched (base 3dcbfd6, implementer a1445fbe9f3bd18c8, sonnet)
Ruling: T2 unchanged usage payload refreshes updated_at in memory only (no disk write per status line tick) — avoids a file write every 300 ms — cost if wrong: after a restart the age shown is from the last change, slightly older than reality
Task 2: review 1 — Important: resets_at unbounded → view() crash persisted. Fix round 1 sent (also minor: mutate _data only after successful save)
Task 2: minor (deferred): no test for usage.changed broadcast on change vs identical repeat
Task 2: minor (deferred): bad updated_at in usage.json discards whole file; redundant _updated guard
Task 2: fix round 1/5 (2 addressed, 0 open; commits 6bfad56..df61d6a)
Task 2: minor (deferred): usage.json updated_at not range-checked on load (only hub writes it; OverflowError not caught in _load)
Task 2: complete (commits 3dcbfd6..df61d6a, review clean)
Task 3: dispatched (base df61d6a, implementer a5798227abdb6d0dd, sonnet)
Task 3: complete (commits df61d6a..8d89224, review clean)
Task 3: minor (deferred): no-op events (ignorable Notification, idle_prompt on finished) still write row + bump last_event_at (affects ordering)
Task 3: minor (deferred): activity after SessionEnd leaves ended_at set → row pruned after linger while running (only SessionStart revives)
Task 3: minor (deferred): fromisoformat on stored strings in rows()/prune could raise on corrupt value; int() on settings
Task 3: minor (deferred): test gaps — norm_path/is_within direct, absolute gitdir, broken gitdir, linger boundary, Store agent round-trip
Task 4: dispatched (base 8d89224, implementer a7424eb414b4634d1, sonnet)
Task 4: complete (commits 8d89224..95919b0, review clean)
Task 4: minor (deferred): tests don't check PATCH/DELETE broadcasts, ignored-event no-broadcast, prune task; 30 s literal not a constant; DELETE broadcasts even for unknown id
Task 5: dispatched (base 95919b0, implementer ac7dd692a7723fefd, sonnet)
Ruling: T5 hub-down latency — Windows retries refused localhost connects (~1.1 s per hook with a 1 s timeout); client uses a 0.25 s connect timeout + 1.0/0.5 s request budget so a downed hub costs ≤ ~0.3 s — design §4.2 assumed "refuses instantly" — cost if wrong: an event lost if a loaded machine takes >250 ms to accept on localhost
Ruling: T6 (carry) make UserPromptSubmit, Notification and PostToolUse hooks async (never block the owner's typing/tool flow); SessionStart, Stop, StopFailure, SessionEnd stay sync (must not be cancelled at teardown) — refines design §4.2 (only PostToolUse async) — cost if wrong: async events may arrive slightly out of order (running vs waiting flicker)
Task 5: pre-review fix sent (connect timeout)
Task 5: review 1 — event mode approved; Important: passthrough subprocess can hang (cmd.exe grandchild holds pipe after timeout); Important: bad resets_at blanks statusline. Fix round 1 sent (+ lazy subprocess import, bool exclusion)
Task 5: minor (deferred): request budgets are per-socket-op not total deadline; statusline worst case ~0.75+2 s undocumented; passthrough decode utf-8 vs OEM; test gaps (statusline hub down, non-dict rate_limits, garbage-stdin posting); free_port race
Task 5: fix round 1/5 (4 addressed, 0 open; commits 92ad02d..77dcd86)
Task 5: minor (deferred): if taskkill fails, pipe.close() could block behind reader thread; stalled taskkill could push statusline to ~4.5 s
Task 5: complete (commits 95919b0..77dcd86, review clean)
Task 6: dispatched (base 77dcd86, implementer ac0d6ebbfd080878e, sonnet; rulings: async events, console python)
Task 6: review 1 (opus) — Important (plan-mandated): whole group deleted if any hook ours; generic marker "hook_client.py"; installed_at microseconds
Ruling: T6 ownership is per-hook (filter our hooks out of groups, drop a group only if emptied) and recognised by the path suffix brainhub/hook_client.py (normalised, case-insensitive) — supersedes plan/design §4.2 "group whose single hook…" + generic MARK; owner-defined hooks/status line must never be lost — cost if wrong: an entry installed from a renamed package folder isn't recognised and must be removed by hand
Task 6: fix round 1 sent (+ minors: odd shapes → IntegrationError, unique backups, ensure_ascii=False, keep pre-existing empty event lists)
Task 6: minor (deferred): unreadable state file on re-install loses saved previous statusLine; stale previous resurrected if owner removed statusLine; no backup pruning; status approximate (partial install, previous inferred); fixed tmp name / no lock for concurrent installs; OSError not translated in CLI
Task 6: fix round 1/5 (7 addressed, 0 open; commits 0809983..4af3766)
Task 6: minor (deferred): ownership match is substring not true suffix (mybrainhub/hook_client.py); MARK constant unused; "hooks": {} original not restored exactly (key dropped)
Task 6: complete (commits 77dcd86..4af3766, review clean)
Task 7: dispatched (base 4af3766, implementer a8136f3c47419966d, sonnet)
Ruling: T7 projects[].open counts all open items incl. next_time; next_time is a subset — the tile caption is 'with open items' — cost if wrong: a project with only next-time notes counts as having open items
Task 7: review 1 — approved but Important: /api/open URL re-validation untested; probe failure-caching/no-reconnect untested. Fix round 1 sent
Task 7: minor (deferred): /widget test branches on file existence (Task 9 must make it unconditional 200); opener OSError → generic 500; compute_today fed dummy lists; [-5:] HH:MM slice depends on usage format; no 1 s timeout test; usage warn → attention untested
Task 7: fix round 1/5 (2 addressed, 0 open; commits 90f366b..eac8393)
Task 7: minor (deferred): probe test rebinds same port after close (possible Windows flake)
Task 7: complete (commits 4af3766..eac8393, review clean)
Task 8: dispatched (base eac8393, implementer a2e9cca1dd0f16b83, sonnet)
Ruling: T8 resetText prefixes MM-DD when the reset isn't today (weekly window resets days away; 'resets 14:20' alone would be wrong) — extends the brief's exact string — cost if wrong: slightly longer text
Task 8: review 1 — Important: rename/Where redraw blocked by typing() guard (edit box lingers, Esc no-op); resetText date branch untested. Fix round 1 sent (+ usage in-flight guard, port advice only when installed)
Task 8: minor (deferred): usage redraw wipes "Couldn't open" error; patch() resets dirty; del() sends {} body; no automated test for Enter/Esc/delete flows
Task 8: fix round 1/5 (4 addressed, 0 open; commits e4a3872..533c4f0)
Task 8: minor (deferred): forced redraw after a blur-PATCH can wipe a different row's freshly focused editor (ms window)
Task 8: complete (commits eac8393..533c4f0, review clean)
Task 9: dispatched (base 533c4f0, implementer aa48e322e3ceb6887, sonnet)
Ruling: T9 fitWindow measures the panel's bounding height, not documentElement.scrollHeight (which never drops below the viewport, so collapse would never shrink the Tauri window) — amends brief interface text — cost if wrong: none
Ruling: T9 usage tile falls back to seven_day when five_hour has no value — design silent — cost if wrong: tile shows weekly % labelled as such
Task 9: pre-review fix sent (fitWindow measurement)
Task 9: review 1 — Important: capture double-submit; refresh destroys focus/errors; drag-region not on children (plan-gap); overlapping refreshes. Fix round 1 sent (+ seven_day fallback test, meter track dark)
Ruling: T9 data-tauri-drag-region goes on the bar and its grip/dot/synopsis children (Tauri v2 checks the event target) — brief put it only on .bar — cost if wrong: none
Controller visual check (Chrome, scratch hub :8096): Agents page, Usage page, widget collapsed/expanded/popup render correctly and legibly; agent <script> title shown as text
Task 9: minor (deferred): tile width implicit 1fr; collapse doesn't clear capture status; Loading… persists on slow first fetch
Task 9: fix round 1/5 (5 addressed, 0 open; commits 9a099f8..716d9bb)
Task 9: minor (deferred): openInApp error path re-renders popup without restoring focus; render() throw skips queued re-run
Task 9: complete (commits 533c4f0..716d9bb, review clean)
Task 10: dispatched (base 716d9bb, implementer a7c80508cfba520b7, opus)
Ruling: T10 a hard-killed/crashed shell leaves the hub it started running (no job object) — the next shell launch finds the port answering and adopts it as external; the hub is the thing that must keep running — cost if wrong: an orphan hub after a crash until logoff (Restart hub disabled for it)
Task 10: note — implementer's smoke test made real mouse clicks / widget popped up on the owner's screen during the run (tell owner)
Task 10: complete (commits 716d9bb..129aa77, review clean)
Task 10: minor (deferred): supervisor lock held during hub_up (UI can block ~0.5 s); spawn test weak on stop; tick invariant child/started_at; port drift — spawned hub lacks --port (worth fixing); restart() no wait for port; open_browser & quoting comment; desktop-config doesn't warn on non-hub root; hub.log unbounded; rust test temp files
Task 11: dispatched (base 129aa77, implementer abeabb73f1fe7bb6f, opus)
Task 11: complete (commits 129aa77..fd57a95; e2e: real claude -p row ready 0.8 s → running 5.1 s → finished 7.2 s → ended 7.5 s → gone 67 s; statusline 0.14 s; tests 425 py / 22 js / 11 rust) — docs task, no separate task review (final review covers it)
Task 11: concerns for final wave: first launch placed at screen bottom so expand grows off-screen; tile captions clipped 1-2 px (descenders) at 125%; port drift (spawned hub lacks --port); WebView2 data dir under real %LOCALAPPDATA%\dev.brainhub.desktop even with LOCALAPPDATA overridden (normal for real use)
Final review (opus, 1e41a5f..fd57a95): With fixes — Critical: pythonw hub flashes consoles; Important: reload drops typed text, port drift, off-screen growth; Minors 5,6,8,9,10,11 included in fix wave. Findings: final-findings.md
Ruling: final wave also fixes minors 5,6,8,9,10,11 (cheap, user-visible) — reviewer marked them nice-to-have — cost if wrong: slightly larger fix diff
Ruling: final reviewer's remaining deferred-minor triage accepted as "park" — documented as residuals in phase2-decisions.md — cost if wrong: owner meets them later
Final fix wave: commits fd57a95..cd699db (145128a hub, b31e18e widget, 68da5e1 shell, cd699db docs); py 436 / js 25 / rust 14, release build 0 warnings; desktop re-check on scratch root OK; note: two scratch captures were filed by real claude on owner's subscription; drag not clamped (only resize)
