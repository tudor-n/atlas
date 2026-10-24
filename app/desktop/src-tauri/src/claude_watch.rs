//! claude-sync while the shell runs (plan Task 11, "watch settings.json while running"; dual-boot
//! design §7.2, "committed in quiet batches by the desktop shell while it runs"; ruling D12).
//!
//! In runtime mode, besides the run before the first hub start (`Prep`), the shell
//! - polls the modification time of Claude Code's settings file every 5 s and, when it changed,
//!   runs `claude-sync --settings-only`, so a setting changed on this OS reaches the shared store
//!   (and its git backup) within seconds;
//! - runs a full `claude-sync` every 10 minutes, so memory notes written during sessions are
//!   committed and pushed in quiet batches.
//!
//! All of it runs on the watch's own thread, never the UI's, and never two at once: a run that
//! falls due while another one runs (the one at start included) waits for a poll after it. Each
//! run uses the current slot's python and the run at start's limits. claude-sync's own write to
//! the settings file is no change: the mtime is read again after each run and remembered.

use crate::supervisor::{current_slot, run_claude_sync, Limits};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, TryLockError};
use std::time::{Duration, Instant, SystemTime};

/// How often the settings file's modification time is read.
pub const POLL: Duration = Duration::from_secs(5);
/// How often a full claude-sync runs.
pub const FULL_EVERY: Duration = Duration::from_secs(600);

/// Which claude-sync is due.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Run {
    /// `claude-sync --settings-only`: the settings file changed.
    SettingsOnly,
    /// `claude-sync`: memory, skills, settings and the commit, every `FULL_EVERY`.
    Full,
}

/// The modification time of the file at `path`, or None when there is none (no path, no file, or
/// unreadable): a file that appears or goes away counts as a change too.
pub fn mtime(path: Option<&Path>) -> Option<SystemTime> {
    std::fs::metadata(path?).and_then(|m| m.modified()).ok()
}

/// When claude-sync is due. Pure: the caller passes the time and the settings file's mtime.
#[derive(Debug)]
pub struct Schedule {
    /// The settings file's mtime when last looked at (inner None: no file); None before the
    /// first look.
    seen: Option<Option<SystemTime>>,
    next_full: Instant,
}

impl Schedule {
    /// The first full run is due `FULL_EVERY` after `now` (the run at start covers the time before).
    pub fn new(now: Instant) -> Self {
        Schedule { seen: None, next_full: now + FULL_EVERY }
    }

    /// The run due at `now`, with the settings file's mtime `mtime`. The first look only records
    /// the mtime: the run at start covers what was there before. A full run covers the settings too.
    pub fn due(&mut self, now: Instant, mtime: Option<SystemTime>) -> Option<Run> {
        let changed = match self.seen {
            None => {
                self.seen = Some(mtime);
                false
            }
            Some(seen) => seen != mtime,
        };
        if now >= self.next_full {
            Some(Run::Full)
        } else if changed {
            Some(Run::SettingsOnly)
        } else {
            None
        }
    }

    /// A run of `run` ended at `now`, and `mtime` is the settings file's mtime read after it,
    /// claude-sync's own write included: that write is no change. A full run starts the next
    /// `FULL_EVERY`.
    pub fn ran(&mut self, run: Run, now: Instant, mtime: Option<SystemTime>) {
        self.seen = Some(mtime);
        if run == Run::Full {
            self.next_full = now + FULL_EVERY;
        }
    }
}

/// The shell's claude-sync runs: their schedule, and the lock that keeps them one at a time.
#[derive(Debug)]
pub struct ClaudeSync {
    schedule: Mutex<Schedule>,
    running: Mutex<()>,
}

impl ClaudeSync {
    pub fn new(now: Instant) -> Self {
        ClaudeSync { schedule: Mutex::new(Schedule::new(now)), running: Mutex::new(()) }
    }

    fn schedule(&self) -> MutexGuard<'_, Schedule> {
        self.schedule.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// One poll of the watch. When a run is due and none is running, `run` it (it returns false
    /// when it couldn't, say without a slot yet: the run then stays due) and remember the settings
    /// file's mtime after it. `now` and `mtime` are the clock and the file-time reader. Returns
    /// the run made, if any.
    pub fn poll(&self, now: impl Fn() -> Instant, mtime: impl Fn() -> Option<SystemTime>,
                run: impl FnOnce(Run) -> bool) -> Option<Run> {
        let _running = match self.running.try_lock() {
            Ok(guard) => guard,
            Err(TryLockError::Poisoned(e)) => e.into_inner(),
            Err(TryLockError::WouldBlock) => return None, // one is running: not now
        };
        let due = self.schedule().due(now(), mtime())?;
        if !run(due) {
            return None;
        }
        self.schedule().ran(due, now(), mtime());
        Some(due)
    }

    /// A full run now: the one before the first hub start. It waits for a running one to end,
    /// and counts as the watch's full run (its mtime, and the next `FULL_EVERY` from its end).
    pub fn run_now(&self, now: impl Fn() -> Instant, mtime: impl Fn() -> Option<SystemTime>, run: impl FnOnce()) {
        let _running = self.running.lock().unwrap_or_else(|e| e.into_inner());
        run();
        self.schedule().ran(Run::Full, now(), mtime());
    }
}

/// The watch: what its thread needs from the supervisor (`Supervisor::claude_watch`).
#[derive(Debug)]
pub struct ClaudeWatch {
    pub sync: Arc<ClaudeSync>,
    /// `desktop.json`'s state folder: the slots, and `ATLAS_STATE_DIR` for claude-sync.
    pub state: PathBuf,
    pub log: PathBuf,
    /// Claude Code's settings file (`config::claude_settings_path`).
    pub settings: Option<PathBuf>,
    pub limits: Limits,
}

impl ClaudeWatch {
    /// Poll every `POLL` for as long as the shell runs.
    pub fn spawn(self) {
        std::thread::spawn(move || loop {
            std::thread::sleep(POLL);
            self.poll_once();
        });
    }

    /// One poll: a due run with the current slot's python (none without a slot: it stays due).
    pub fn poll_once(&self) -> Option<Run> {
        self.sync.poll(Instant::now, || mtime(self.settings.as_deref()), |run| {
            let Some((python, app)) = current_slot(&self.state) else { return false };
            let (settings_only, why) = match run {
                Run::SettingsOnly => (true, "settings changed"),
                Run::Full => (false, "every 10 minutes"),
            };
            run_claude_sync(&python, &app, &self.state, &self.log, settings_only, why, self.limits);
            true
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::time::UNIX_EPOCH;

    fn at(secs: u64) -> Option<SystemTime> {
        Some(UNIX_EPOCH + Duration::from_secs(secs))
    }

    #[test]
    fn settings_change_runs_once_and_own_write_is_ignored() {
        let t0 = Instant::now();
        let (clock, file) = (Cell::new(t0), Cell::new(at(100)));
        let runs = RefCell::new(Vec::new());
        let sync = ClaudeSync::new(t0);
        // One poll, 5 s after the last; the run (if any) writes the file itself when `writes`.
        let poll = |writes: Option<SystemTime>| {
            clock.set(clock.get() + POLL);
            sync.poll(|| clock.get(), || file.get(), |run| {
                runs.borrow_mut().push(run);
                if writes.is_some() {
                    file.set(writes);
                }
                true
            })
        };
        assert_eq!(poll(None), None, "the first look only records the mtime");
        assert_eq!(poll(None), None);
        file.set(at(200)); // the owner changes a setting
        assert_eq!(poll(at(300)), Some(Run::SettingsOnly)); // claude-sync writes the file too
        assert_eq!(poll(None), None, "claude-sync's own write is no change");
        assert_eq!(poll(None), None);
        assert_eq!(*runs.borrow(), [Run::SettingsOnly], "one run for one change");
        // A file that goes away (or appears) counts as a change.
        file.set(None);
        assert_eq!(poll(None), Some(Run::SettingsOnly));
        assert_eq!(poll(None), None);
        file.set(at(400));
        assert_eq!(poll(None), Some(Run::SettingsOnly));
    }

    #[test]
    fn a_run_that_could_not_start_stays_due() {
        let t0 = Instant::now();
        let file = Cell::new(at(100));
        let sync = ClaudeSync::new(t0);
        assert_eq!(sync.poll(|| t0, || file.get(), |_| true), None);
        file.set(at(200));
        // No slot yet: nothing ran, and the change is still due at the next poll.
        assert_eq!(sync.poll(|| t0, || file.get(), |_| false), None);
        assert_eq!(sync.poll(|| t0, || file.get(), |_| true), Some(Run::SettingsOnly));
    }

    #[test]
    fn never_two_at_once() {
        let t0 = Instant::now();
        let file = Cell::new(at(100));
        let sync = ClaudeSync::new(t0);
        assert_eq!(sync.poll(|| t0, || file.get(), |_| true), None);
        file.set(at(200));
        let mut inner = Some(Run::Full);
        // While the run at start runs, a due poll doesn't run; the run at start's own write is
        // remembered after it, so nothing is left due.
        sync.run_now(|| t0, || file.get(), || {
            inner = sync.poll(|| t0, || file.get(), |_| panic!("a second run started"));
            file.set(at(300));
        });
        assert_eq!(inner, None);
        assert_eq!(sync.poll(|| t0, || file.get(), |_| true), None);
    }

    #[test]
    fn full_sync_every_ten_minutes() {
        let t0 = Instant::now();
        let secs = |s: u64| t0 + Duration::from_secs(s);
        let ten = FULL_EVERY.as_secs();
        let mut s = Schedule::new(t0);
        let m = at(100);
        assert_eq!(s.due(secs(5), m), None);
        assert_eq!(s.due(secs(ten - 1), m), None);
        assert_eq!(s.due(secs(ten), m), Some(Run::Full));
        // Not run yet (another run going, or no slot): still due at the next poll.
        assert_eq!(s.due(secs(ten + 5), m), Some(Run::Full));
        // A changed file while a full run is due: the full run covers it.
        assert_eq!(s.due(secs(ten + 10), at(200)), Some(Run::Full));
        s.ran(Run::Full, secs(ten + 40), at(250));
        assert_eq!(s.due(secs(ten + 45), at(250)), None, "its own write is no change");
        // The next one is 10 minutes after the last one ended.
        assert_eq!(s.due(secs(2 * ten + 39), at(250)), None);
        assert_eq!(s.due(secs(2 * ten + 40), at(250)), Some(Run::Full));
        // A settings-only run doesn't move the cadence.
        let mut s = Schedule::new(t0);
        assert_eq!(s.due(secs(5), m), None);
        assert_eq!(s.due(secs(60), at(200)), Some(Run::SettingsOnly));
        s.ran(Run::SettingsOnly, secs(61), at(201));
        assert_eq!(s.due(secs(ten), at(201)), Some(Run::Full));

        // The run at start counts as the full run: the next one is 10 minutes after it.
        let sync = ClaudeSync::new(t0);
        sync.run_now(|| secs(90), || m, || ());
        assert_eq!(sync.poll(|| secs(ten), || m, |_| true), None);
        assert_eq!(sync.poll(|| secs(ten + 90), || m, |_| true), Some(Run::Full));
    }
}
