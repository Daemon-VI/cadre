// Model-written text shown outside the webview (notifications, modals, quick picks).
//
// VS Code renders `[label](target)` in notification messages as a clickable link, and a target
// may be `command:` — so an agent's question containing `[ok](command:…)` would become a button
// that runs a command. Breaking the `](` pair keeps the text readable and makes it inert.
export function inert(text: string): string {
  return text.replace(/\]\s*\(/g, "] (");
}

/** Inert, single-line-ish, and short enough for a notification. */
export function notificationText(text: string, max = 400): string {
  const flat = inert(text).replace(/\s*\n\s*/g, " · ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}
