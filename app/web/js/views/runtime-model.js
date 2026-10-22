// Settings' quiet "Runtime" line (dual-boot plan Task 9): pure, no DOM.
// `runtime` is /api/state.runtime: {commit, slot, checked_at, pending, shell_update}, or null when
// the hub doesn't run from a runtime slot (dev or legacy), which shows no line at all.

const whenText = (iso) => new Date(iso).toLocaleString();

export function runtimeText(runtime, when = whenText) {
  if (!runtime) return '';
  const commit = runtime.commit ? runtime.commit.slice(0, 7) : 'unknown commit';
  const slot = runtime.slot ? ` (slot ${runtime.slot})` : '';
  return [
    `Runtime: ${commit}${slot}`,
    runtime.checked_at ? `checked ${when(runtime.checked_at)}` : 'not checked yet',
    runtime.pending ? `update pending: ${runtime.pending}` : '',
    runtime.shell_update ? 'widget update available' : '',
  ].filter(Boolean).join(' · ');
}
