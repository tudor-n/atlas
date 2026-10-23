# BrainHub Phase 4: Voice and sass (design)

Date: 2026-10-23
Status: **draft, waiting for the owner's approval.** Brainstormed with the owner the same day; their answers are marked **(owner)**.
Parent spec: `2026-10-20-brainhub-design.md` (§8 "Notifications and voice", §12 item 4). It is the authority; this document makes phase 4 concrete. The dual-boot design (§8.3: "voice plays on both OSes") adds the cross-OS requirement. Where this document departs from either, the departure is marked **(my call)**.

## 1. Scope

Phase 4 gives the hub a voice, which speaks through the PC speakers:
1. **Kokoro TTS, local and offline**, in the voice the owner already uses for Atlas: a blend of 70 % `bm_george` and 30 % `bm_fable`, British English, at speed 0.95 **(owner: "use the atlas-jv2 voice")**.
2. **It speaks for only three things** (spec §8):
   - **an agent needs you**: always;
   - **an agent finished**: only after a run of at least 2 minutes, a threshold you can change **(owner)**;
   - **a reminder is due**;
   - **a readback of what was just typed on the tablet**.
3. **Sass:** pre-written quotes with no AI, at least 100 of them, in a **mix of three styles: dry butler, sarcastic friend and ship's computer** **(owner)**. The information comes first and the quip after. Templates are filled with real values.
4. **Controls:**
   - voice on/off (in Settings and on the widget);
   - sass on/off, and sass on readbacks (off by default);
   - which events speak;
   - voice blend, speed and volume, with **Play sample**;
   - quiet hours (the existing `quiet_hours`);
   - **Pause voice** in the tray: for 1 hour, or until morning;
   - editing the quote file.

Not in phase 4:
- **Desktop pop-ups** (KDE notifications): **no (owner)**. Notifications stay in the widget, the app, the tablet and the voice.
- Any speech input or conversational voice (spec anti-goal §1).
- Speaking through the tablet's speaker. The voice is the PC's.
- Google Calendar (phase 5) and the phone (phase 6).
- Windows checks by hand. **Windows is on hold (owner).** The code stays portable, and CI tests both OSes.

## 2. Verified facts (spike, 2026-10-23, Kubuntu)

| Fact | Result | Source |
|---|---|---|
| Atlas's voice | `KPipeline(lang_code='b')`, blend `{'bm_george': 0.7, 'bm_fable': 0.3}`, `speed=0.95`, using the PyTorch `kokoro` package on CUDA | `/mnt/data/atlas_jv2/atlas-backend/core/senses/voice.py`, `config.py` |
| `kokoro-onnx` on Python 3.14 | installs cleanly: `kokoro-onnx 0.6.1`, `onnxruntime 1.31.0`, `phonemizer`, `espeakng-loader` (it bundles espeak-ng, so there's no apt dependency). The venv is 152 MB. | scratch venv |
| The same voices without PyTorch | `voices-v1.0.bin` holds the same style vectors. A blend is `0.7·george + 0.3·fable`, and it plays in `lang="en-gb"`. | spike script |
| Speed, CPU only | the model loads in 0.5–0.6 s. A 5–6 s line takes **0.7–0.9 s** with the int8 model (92 MB) and **1.1–1.3 s** with fp32 (326 MB). No GPU is needed. | spike, 28 threads |
| Model files | GitHub release `thewh1teagle/kokoro-onnx` `model-files-v1.0`: `kokoro-v1.0.int8.onnx` (sha256 `6e7421…6406cb`), `kokoro-v1.0.onnx` (`7d5df8…36a6c5`), `voices-v1.0.bin` (`bca610…f1fbf7d`) | `sha256sum` |
| Audio | PipeWire with a PulseAudio front (`pw-play`, `paplay`, `aplay` present). Windows has the stdlib `winsound`. | `pactl info`, `which` |
| Events to hook | `AgentTracker` already moves rows to `waiting` and `finished`, with `status_since`. Items carry `due`, `remind_at` and `snooze_until`. Tablet notes and answers arrive through `tablet_routes`. | `agents.py`, `review.py`, `tablet_routes.py` |
| Settings | `quiet_hours` already exists (`["23:00","08:00"]`, validated) and is unused until now | `config.py` |
| Tray | Open, Show/Hide widget, Start at login, Restart hub, Quit: there's no pause item yet | `desktop/src-tauri/src/lib.rs` |

Still to check during the build: the int8 model sounds as good as fp32 to the owner (§11), and the Windows wheels of `onnxruntime`/`espeakng-loader` for 3.14 install in CI.

## 3. Architecture

```
hub events ──► Announcer (rules: on/off, which events, quiet hours, pause, thresholds, dedupe, merge)
  agents.changed                        │ a line = info + optional quip (Quotes)
  reminder tick (30 s)                  ▼
  tablet note / answer          Speech queue (one at a time; stale lines dropped)
                                        │ JSON lines over stdin/stdout
                                        ▼
                     voice worker  `python -m brainhub voice-worker`   (child process, on demand)
                       kokoro-onnx → WAV in the state folder → player (pw-play | paplay | aplay; winsound)
```

- **(my call) The voice runs in a child process**, not inside the hub. Its model takes memory (about 300 MB with int8), so it starts on the first line and exits after 10 minutes with nothing to say. A crash or a missing audio device never takes the hub down: the line is dropped, logged, and shown as one quiet line in Settings ("voice: no audio device").
- **(my call) `kokoro-onnx`, not PyTorch `kokoro`.** It gives the same voices and the same blend with no 2 GB torch and no CUDA, and it runs fast enough on CPU. It becomes a normal hub dependency, so both runtime slots and CI get it.
- **The child is killed with the hub**, through a process group on POSIX and `taskkill /T` on Windows (the portability rules).

## 4. When it speaks (the Announcer, `brainhub/voice.py`)

### 4.1 Events
| Event | Trigger | Line (info first) |
|---|---|---|
| `agent_waiting` | a row enters `waiting` (permission, idle prompt or error). Once per waiting stretch. | "{project} needs you." Plus "It's asking permission." for a permission prompt. |
| `agent_finished` | `running` → `finished`, where the running stretch lasted ≥ `voice_finish_min_s` (default 120) | "{project} finished, after {duration}." |
| `reminder` | an open item's reminder time passes while the hub runs (§4.3) | "Reminder: {text}." with ", due {when}" when it has a due date |
| `readback` | a tablet note or measurement answer is saved | answer: "{label}, {value} {unit words}. Saved." (one clause per field). Note: "Note saved: {text}." |

- **The voice never reads agent-supplied text** (titles, notification messages, branch names). It reads only the project name the owner gave in the map, or the folder name when there isn't one. Agent text is untrusted (rule 6), and reading it aloud would be both noisy and odd.
- `{text}` is the owner's own words: item text, tablet notes. Lines are capped at about 200 characters, and longer ones end with "… and more".
- Units are spoken as words: mm → millimetres, ° → degrees, Ω → ohms, and so on. The map is in `voice.py`, and unknown units are read as written.
- Sessions the hub ignores (rule 13) never speak.

### 4.2 Gates (all must pass)
1. `voice_enabled` is on, and the voice files are installed (§6).
2. The event type is ticked in `voice_events`.
3. It's not quiet hours. Quiet hours wrap past midnight, `["23:00","08:00"]` included. **(my call)** A line suppressed by quiet hours is not saved for later: Today and the widget already show it.
4. Voice isn't paused (tray, §7).
5. **Dedupe:** the same agent/item/event is never spoken twice for the same stretch or reminder time.

### 4.3 Reminders
- An item's reminder time is `snooze_until`, otherwise `remind_at`, otherwise `due`. Only open, confirmed items count (spec: nothing unconfirmed speaks).
- **(my call)** A date with no time is spoken at the **end of quiet hours** (08:00 by default), or at the first tick after it if the laptop was off.
- The hub ticks every 30 s. It speaks the items whose reminder time falls in `(last_tick, now]`, and stores `last_tick` in the state folder's `voice.json`. So a restart never repeats a reminder, and the other OS has its own state.
- **Startup catch-up (my call):** reminders that fell due while the hub was off become **one** line, "You have 3 reminders from while I was off.", never a pile of lines. More than 24 hours missed counts as none: Today's Overdue covers it.

### 4.4 The queue
- One line plays at a time. At most 5 wait; past that the oldest are dropped, and a line older than 60 s is dropped instead of played.
- **(my call) Merge:** lines of the same event type arriving within 3 s of each other become one ("Two agents finished: robotarm and BrainHub.").
- Turning the voice off, or pausing it, empties the queue and stops the current line.

## 5. Sass (`brain/_voice/quotes.md`)

- **One plain file that the owner edits**, in the brain, so it's backed up and shared by both OSes. **(my call)** `_voice/` becomes a protected path, like `_items/`: the filing agent can't touch it.
- The hub seeds it on first start when it's missing, and commits that. It never overwrites the file.
- Format: a `## <event>` heading per pool (`agent_waiting`, `agent_finished`, `reminder`, `readback`, `catch_up`), then one quote per line. Lines starting with `#` are comments. Placeholders are `{project}`, `{duration}`, `{count}`, `{text}` and `{time_of_day}`.
- **At least 100 quotes**, spread across the pools and mixed across the three styles **(owner)**: dry butler, sarcastic friend, ship's computer. They're written once, during the build.
- **No repeats until a pool has cycled:** each pool is shuffled, and the position is kept in the state folder's `voice.json`. Editing the file reshuffles that pool.
- A quote whose placeholder can't be filled is skipped. A broken file is never fatal: the info line still plays, and Settings shows one quiet warning with the line number.
- **(my call) A one-minute cooldown on quips:** after a quip, the next line within 60 s plays without one, so a busy stretch doesn't turn into a comedy set.
- Sass on readbacks is off by default (spec §8).

## 6. The voice files (download once, shared)

- The model and voices aren't in git. Settings → **Voice** → **Download voice (about 120 MB)** fetches `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin` from the pinned release URLs.
  - It checks each file's sha256 before using it.
  - Files go into shared `data/voice/`, so both OSes use one download (OS-neutral files, dual-boot §8.3).
  - It writes to a `.part` file and renames it after the check passes.
- **(my call) The int8 model is the default.** It's faster and a quarter of the size. If the owner hears a difference in the samples (§11), Settings offers "Higher quality (330 MB)" for fp32.
- Until the files are present, voice shows "not installed" and nothing is spoken. No other feature depends on it.

## 7. Controls

### 7.1 Settings (shared `data/settings.json`, validated)
| Key | Default | Rule |
|---|---|---|
| `voice_enabled` | `true` (inert until the files are installed) | bool |
| `voice_events` | `{"agent_waiting": true, "agent_finished": true, "reminder": true, "readback": true}` | those four keys, bools |
| `voice_finish_min_s` | `120` | 0–3600 |
| `voice_sass` | `true` | bool |
| `voice_sass_readback` | `false` | bool (spec: off by default) |
| `voice_blend` | `{"bm_george": 0.7, "bm_fable": 0.3}` | 1–3 Kokoro voice ids from the voices file, with weights > 0 that are normalised to sum to 1 |
| `voice_speed` | `0.95` | 0.5–2.0 |
| `voice_volume` | `80` | 0–100 (applied to the samples, so it's the same on both OSes) |
| `voice_model` | `"int8"` | `int8` or `fp32` |
| `quiet_hours` | `["23:00","08:00"]` (existing) | unchanged |

Settings → **Voice** gets:
- the switches;
- a blend editor (voice dropdowns with weights) and **Play sample**;
- speed and volume sliders;
- the download button with its state;
- **Edit quotes**, which opens `brain/_voice/quotes.md` in the editor the OS uses for `.md` files (`xdg-open`; on Windows the default app).

### 7.2 The widget and the tray
- **Widget:** a speaker icon that toggles `voice_enabled` (spec §2: "a voice on/off toggle is reachable from the widget"). It is struck through while off or paused.
- **Tray:** **Pause voice** opens a submenu with "For 1 hour", "Until morning" (the end of quiet hours) and "Resume". The pause is per-OS state (`voice.json`), so it doesn't follow you to the other OS. This is the spec's "pause notifications": the voice is the only audible channel. The shell calls a new local endpoint. While a pause runs, the menu shows "Voice paused until 14:05".

## 8. Playback per OS
- **Linux:** the first of `pw-play`, `paplay` and `aplay` on PATH plays a temp WAV in the state folder. It runs in its own process group, so "stop" kills it cleanly.
- **Windows:** `winsound.PlaySound(path, SND_FILENAME)` in the worker. Stop is `PlaySound(None, 0)`. No new dependency.
- Volume is applied by scaling the samples before the WAV is written.
- No player found → the "voice: no audio player" warning. Tests use a fake player on both OSes.

## 9. API summary
| Method | Path | What |
|---|---|---|
| GET | `/api/voice` | state: installed, model, enabled, paused until, last error, quote-file warnings |
| POST | `/api/voice/download` | starts the download, with progress over the WebSocket (`voice.download`) |
| POST | `/api/voice/sample` | speaks a fixed sample in the current (or the posted, unsaved) blend, speed and volume, ignoring quiet hours and pause |
| POST | `/api/voice/pause` | `{minutes}` or `{until: "morning"}` or `{resume: true}` |
| POST | `/api/voice/stop` | stops the current line and empties the queue |

All of them are PC-only, under `local_only` and outside `/api/tablet/` (rule 5).

## 10. Data and versions
- **No `hub.db` schema change.** The voice keeps its small state in the per-OS state folder (`voice.json`: `last_tick`, pause, quote positions).
- New shared files: `data/voice/*`, which is git-ignored like the rest of `data/`, and `brain/_voice/quotes.md`, a protected path.
- New dependency: `kokoro-onnx`, pinned in `uv.lock` (it brings `onnxruntime`, `phonemizer` and `espeakng-loader`).

## 11. Rollout (with the owner, after the merge)
1. The widget update pulls the merge. Settings → **Voice** → **Download voice**.
2. **Listen:** press Play sample with the int8 model, then with fp32. The owner picks one.
3. Check a line of each kind: an agent that needs you (any permission prompt), an agent that finishes after 2 minutes, a reminder set for a minute ahead, and a tablet note.
4. Try the tray pause, then quiet hours (set them to start a minute from now).
5. Read through the quote file and change what doesn't land.

## 12. Testing
- **Python (fake clock, fake engine, fake player, on both OSes in CI):**
  - each gate;
  - quiet hours across midnight;
  - pause, and resume;
  - the finish threshold;
  - dedupe per stretch and per reminder time;
  - reminders: snooze, date-only at the end of quiet hours, restart with no repeats, startup catch-up as one line, more than 24 h missed;
  - merge and the stale/oversize queue;
  - readback wording with units;
  - never reading agent text;
  - quotes: parsing, cycling without repeats, a placeholder that can't be filled, a broken file, the cooldown;
  - the worker protocol and its idle exit;
  - player selection per OS;
  - the download: checksum mismatch, `.part` rename, resume after a failure, served by a local test server;
  - the settings' validation;
  - the endpoints are refused from the LAN.
- **JS:** the voice section's pure model (blend weights, states), and the widget toggle.
- **Rust:** the tray pause submenu and its label.
- **By hand on Kubuntu (dev copy, temp root):** real synthesis and playback with the real model, a sample in the Atlas blend, the worker's idle exit, and killing the hub while it speaks.

## 13. Left to the builder
- The quote texts (at least 100, in three styles, spread across five pools).
- The exact wording of the info lines, within §4.1.
- The duration wording ("42 minutes", "an hour and 10 minutes").
