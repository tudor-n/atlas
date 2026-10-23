# BrainHub Phase 4.5 (native window, instrument-panel UI): build plan

Date: 2026-10-24. Design: `docs/superpowers/specs/2026-10-24-brainhub-phase4_5-design.md`. Branch `phase-4.5`.

How it's built:
- **Subagents** (the owner's instruction), in waves. Agents don't commit; the lead commits each task with a plain message and no trailers.
- **Per-view CSS** lives in `web/css/views/<view>.css`, so parallel agents never edit the same file.
- **Build-time calls** go into `docs/superpowers/phase4_5-decisions.md` (rulings N1…).
- **At the end:** three subagent reviews, every finding fixed, then push and a PR. **The owner merges.**

## Waves
**Wave A, in parallel:**
- **Foundation:** fonts, tokens, base, components, `press-model`/`press`, `nav-model`, `status-model`, `ui.js`, and the guard test.
- **Shell and desktop-entry:** the Rust main window, the `open_main` command, window state, `--open`, the tray, `main.html`, the digest including `dist`, and the `desktop-entry` CLI.

**Wave B:** the app shell (`index.html`, `app.css`, the top bar and status cluster, the capture bar, the tray rows, reduced and read-only states, `app.js` on `nav-model`). It also stubs the routes `browse` and `settings/<section>`.

**Wave C, in parallel:**
1. Confirm card and Today.
2. Ask and Journal.
3. Agents with Usage, Tablet, Browse.
4. Settings and Setup.
5. The widget, including its `invoke('open_main')` path.
6. Tablet drift fixes.

**Wave D:**
- Verification screenshots at real sizes (headless Chrome, a scratch hub).
- Docs: README, CLAUDE.md, NEXT-SESSION, TODO, decisions.
- Three reviews (the arm language and UX; rule 6, the CSP and code; the shell, platform and tests), then the fixes, the push and the PR.
