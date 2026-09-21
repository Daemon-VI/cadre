// The webview's document, its message validation, and static guarantees about the sources.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import { contentSecurityPolicy, makeNonce, panelHtml, runPanelHtml } from "../../src/html";
import { isWebviewMessage } from "../../src/protocol";

const ROOT = path.resolve(__dirname, "..", "..", "..");

test("CSP: nonce'd script only, nothing inline, nothing remote", () => {
  const csp = contentSecurityPolicy("https://file+.vscode-resource.vscode-cdn.net", "N0nce");
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /script-src 'nonce-N0nce'(;|$)/);
  assert.ok(!csp.includes("unsafe-inline") && !csp.includes("unsafe-eval"));
  assert.ok(!/connect-src/.test(csp), "the webview makes no requests");
});

test("the document carries no data, one script with the nonce, and no inline script or style", () => {
  const nonce = makeNonce();
  const html = runPanelHtml({ cspSource: "vscode-src", nonce, scriptUri: "vscode-src/run.js", styleUri: "vscode-src/run.css" });
  const scripts = html.match(/<script\b[^>]*>/g) ?? [];
  assert.equal(scripts.length, 1);
  assert.ok(scripts[0].includes(`nonce="${nonce}"`) && scripts[0].includes('src="vscode-src/run.js"'));
  assert.ok(/<script[^>]*><\/script>/.test(html), "the script tag is empty (no inline code)");
  assert.ok(!/<style\b|\sstyle=/.test(html));
  assert.ok(!/token/i.test(html));
});

test("attribute values are escaped", () => {
  const html = runPanelHtml({ cspSource: "x", nonce: "n", scriptUri: 'a"><img src=x>', styleUri: "s" });
  assert.ok(!html.includes('"><img'));
});

test("nonces are fresh and CSP-safe", () => {
  const a = makeNonce();
  const b = makeNonce();
  assert.notEqual(a, b);
  assert.match(a, /^[A-Za-z0-9_-]{24}$/);
});

test("the host acts only on known webview messages", () => {
  for (const ok of [{ type: "ready" }, { type: "review" }, { type: "cancel" }, { type: "resume" }, { type: "approvals" },
    { type: "approval", id: "a1b2c3" }]) assert.ok(isWebviewMessage(ok), JSON.stringify(ok));
  for (const bad of [null, "ready", {}, { type: "decide", approve: true }, { type: "approval" }, { type: "approval", id: "" },
    { type: "approval", id: "../../x" }, { type: "approval", id: 5 }]) assert.ok(!isWebviewMessage(bad), JSON.stringify(bad));
});

test("no source file parses text as HTML", () => {
  const offenders: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (/\.ts$/.test(entry.name) && /\.(innerHTML|outerHTML)\b|insertAdjacentHTML|document\.write/.test(fs.readFileSync(p, "utf8"))) {
        offenders.push(path.relative(ROOT, p));
      }
    }
  };
  walk(path.join(ROOT, "src"));
  assert.deepEqual(offenders, []);
});

test("a panel document: the same CSP, one nonce'd script, both stylesheets, an escaped title", () => {
  const nonce = makeNonce();
  const html = panelHtml({
    cspSource: "vscode-src", nonce, scriptUri: "vscode-src/panels.js",
    styleUris: ["vscode-src/run.css", "vscode-src/panels.css"], title: 'Cadre · <Usage> & "more"',
  });
  const scripts = html.match(/<script\b[^>]*>/g) ?? [];
  assert.equal(scripts.length, 1);
  assert.ok(scripts[0].includes(`nonce="${nonce}"`) && scripts[0].includes('src="vscode-src/panels.js"'));
  assert.ok(/<script[^>]*><\/script>/.test(html), "no inline code");
  assert.ok(html.includes(contentSecurityPolicy("vscode-src", nonce).replace(/'/g, "'")));
  assert.equal((html.match(/<link rel="stylesheet"/g) ?? []).length, 2);
  assert.ok(html.includes("<title>Cadre · &lt;Usage&gt; &amp; &quot;more&quot;</title>"));
  assert.ok(!/<style\b/.test(html) && !/\sstyle=/.test(html), "no inline style");
});
