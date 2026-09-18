// The run-detail webview's document. Static: no run data is ever written into it — the data
// arrives afterwards by postMessage and is inserted with textContent (ADR-029). The CSP allows
// one script (by nonce, from the extension's own folder), one stylesheet, and nothing remote.
import { randomBytes } from "node:crypto";

export function makeNonce(): string {
  return randomBytes(18).toString("base64url");
}

function attr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function contentSecurityPolicy(cspSource: string, nonce: string): string {
  return [
    "default-src 'none'",
    `script-src 'nonce-${nonce}'`,
    `style-src ${cspSource}`,
    `img-src ${cspSource}`,
    `font-src ${cspSource}`,
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

export function runPanelHtml(o: { cspSource: string; nonce: string; scriptUri: string; styleUri: string }): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${attr(contentSecurityPolicy(o.cspSource, o.nonce))}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${attr(o.styleUri)}">
<title>Cadre run</title>
</head>
<body>
<main id="app"><p class="muted">Loading…</p></main>
<script nonce="${attr(o.nonce)}" src="${attr(o.scriptUri)}"></script>
</body>
</html>`;
}
