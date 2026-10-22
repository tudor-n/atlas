// Drive banner text (dual-boot design §6.5): pure, no DOM, shared by the app shell and the widget.
// `drive` is {state, message, held} from /api/state or /api/widget, or null while the root is fine.

export function heldText(n) {
  const k = Number(n) || 0;
  if (k <= 0) return '';
  return k === 1 ? '1 capture held on this OS' : `${k} captures held on this OS`;
}

export function driveText(drive) {
  if (!drive) return '';
  return [drive.message, heldText(drive.held)].filter(Boolean).join(' · ');
}
