//! The widget follows the main window (owner, 2026-10-24): the widget is what ATLAS looks like
//! minimised.
//!
//! | main window | widget |
//! |---|---|
//! | open (normal or maximised, focused or not) | hidden |
//! | minimised | shown |
//! | hidden (closed: it only hides) | hidden |
//! | not opened yet (a login autostart, a plain start) | shown, as before |
//!
//! The shell acts only when the main window's state changes (`decide`), so the tray's
//! Show/Hide widget still works as an override until the next change.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainState {
    NotOpened,
    Open,
    Minimized,
    Hidden,
}

/// Should the widget show while the main window is in `state`?
pub fn widget_wanted(state: MainState) -> bool {
    matches!(state, MainState::NotOpened | MainState::Minimized)
}

/// The main window's state from what can be observed of it.
pub fn state_of(exists: bool, visible: bool, minimized: bool) -> MainState {
    match (exists, visible, minimized) {
        (false, _, _) => MainState::NotOpened,
        (true, false, _) => MainState::Hidden,
        (true, true, true) => MainState::Minimized,
        (true, true, false) => MainState::Open,
    }
}

/// `Some(show the widget?)` when the state moved from `last` to `now`; `None` when nothing changed.
pub fn decide(last: Option<MainState>, now: MainState) -> Option<bool> {
    (last != Some(now)).then(|| widget_wanted(now))
}

#[cfg(test)]
mod tests {
    use super::*;
    use MainState::*;

    #[test]
    fn the_widget_shows_only_while_the_app_is_minimised_or_not_opened() {
        assert!(widget_wanted(Minimized) && widget_wanted(NotOpened));
        assert!(!widget_wanted(Open) && !widget_wanted(Hidden));
    }

    #[test]
    fn states() {
        assert_eq!(state_of(false, true, true), NotOpened);
        assert_eq!(state_of(true, false, true), Hidden); // hidden wins over a stale minimised flag
        assert_eq!(state_of(true, true, true), Minimized);
        assert_eq!(state_of(true, true, false), Open);
    }

    #[test]
    fn it_acts_on_changes_only() {
        assert_eq!(decide(None, NotOpened), Some(true));
        assert_eq!(decide(Some(NotOpened), Open), Some(false));
        assert_eq!(decide(Some(Open), Open), None); // the tray's override stands
        assert_eq!(decide(Some(Open), Minimized), Some(true));
        assert_eq!(decide(Some(Minimized), Open), Some(false));
        assert_eq!(decide(Some(Open), Hidden), Some(false));
        assert_eq!(decide(Some(Hidden), Open), Some(false));
    }
}
