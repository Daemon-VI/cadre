// The detached launch, with Node itself standing in for `cadre serve`.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { after, test } from "node:test";
import type { Invocation } from "../../src/cli";
import { launchDetached, waitFor } from "../../src/launch";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cadre-launch-"));
after(() => fs.rmSync(dir, { recursive: true, force: true }));

const node = (code: string): Invocation => ({ command: process.execPath, args: ["-e", code], via: "cadre" });

test("output goes to the log file (appended), and the exit code is reported", async () => {
  const log = path.join(dir, "nested", "server.log");
  const first = launchDetached(node("console.log('first'); console.error('to stderr')"), log);
  assert.ok(first.pid);
  assert.ok(await waitFor(async () => first.exitCode() !== undefined, { limitMs: 10_000, everyMs: 50 }));
  assert.equal(first.exitCode(), 0);
  const second = launchDetached(node("process.exit(3)"), log);
  assert.ok(await waitFor(async () => second.exitCode() !== undefined, { limitMs: 10_000, everyMs: 50 }));
  assert.equal(second.exitCode(), 3);
  const text = fs.readFileSync(log, "utf8");
  assert.match(text, /first/);
  assert.match(text, /to stderr/);
});

test("a large old log is truncated before appending", async () => {
  const log = path.join(dir, "big.log");
  fs.writeFileSync(log, "x".repeat(2000));
  const l = launchDetached(node("console.log('fresh')"), log, process.env, 1000);
  assert.ok(await waitFor(async () => l.exitCode() !== undefined, { limitMs: 10_000, everyMs: 50 }));
  assert.equal(fs.readFileSync(log, "utf8").trim(), "fresh");
});

test("a missing executable reports an error instead of hanging", async () => {
  const l = launchDetached({ command: path.join(dir, "no-such-cadre.exe"), args: [], via: "cadre" }, path.join(dir, "e.log"));
  assert.ok(await waitFor(async () => l.exitCode() !== undefined, { limitMs: 10_000, everyMs: 50 }));
  assert.equal(l.exitCode(), -1);
  assert.match(l.error() ?? "", /ENOENT/);
});

test("waitFor gives up on stop() or at the limit", async () => {
  assert.equal(await waitFor(async () => false, { limitMs: 5_000, everyMs: 10, stop: () => true }), false);
  const t0 = Date.now();
  assert.equal(await waitFor(async () => false, { limitMs: 200, everyMs: 20 }), false);
  assert.ok(Date.now() - t0 < 2000);
  let n = 0;
  assert.equal(await waitFor(async () => ++n >= 3, { limitMs: 5_000, everyMs: 10 }), true);
});
