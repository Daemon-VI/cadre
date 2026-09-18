// The API client against a real HTTP server on an ephemeral loopback port that behaves like
// `cadre serve` in the ways the client depends on: bearer auth everywhere but /health, a loopback
// Host check, FastAPI-style error bodies, and a chunked text/event-stream.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import type { AddressInfo } from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, test } from "node:test";
import { ApiError, CadreClient, detailMessage, readTokenFile, ServerUnreachable, TokenMissing } from "../../src/api";

const TOKEN_A = "test-token-A-0123456789abcdef";
const TOKEN_B = "test-token-B-fedcba9876543210";
let serverToken = TOKEN_A;

interface Seen { method: string; url: string; auth?: string; host?: string; body: string }
const seen: Seen[] = [];

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    seen.push({ method: req.method ?? "", url: req.url ?? "", auth: req.headers.authorization, host: req.headers.host, body });
    const json = (status: number, obj: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    const host = (req.headers.host ?? "").replace(/:\d+$/, "");
    if (!["127.0.0.1", "localhost"].includes(host)) return json(421, { detail: "host not allowed" });
    if (req.url === "/api/v1/health") return json(200, { ok: true, version: "1.0.0" });
    if (req.headers.authorization !== `Bearer ${serverToken}`) return json(401, { detail: "missing or wrong bearer token" });
    const url = new URL(req.url ?? "/", "http://x");
    switch (`${req.method} ${url.pathname}`) {
      case "GET /api/v1/orgs":
        return json(200, [{ name: "decision-board", source: "template", valid: true }]);
      case "GET /api/v1/approvals":
        return json(200, url.searchParams.get("pending") === "true" ? [{ id: "a1", run_id: "r1", kind: "exec", prompt: "p", status: "pending", created: 1 }] : []);
      case "POST /api/v1/approvals/a1":
        return json(200, { id: "a1", approved: JSON.parse(body).approve });
      case "POST /api/v1/approvals/gone":
        return json(409, { detail: "already decided" });
      case "POST /api/v1/runs": {
        const b = JSON.parse(body);
        if (b.project === "/dirty") return json(400, { detail: "/dirty has uncommitted changes. Commit or stash them, or pass --allow-dirty" });
        if (!b.org) return json(422, { detail: { errors: ["org: unknown", "goal: empty"] } });
        return json(200, { id: "20260918-120000-abc123" });
      }
      case "POST /api/v1/forecast":
        return json(422, { detail: [{ loc: ["body", "goal"], msg: "Field required", type: "missing" }] });
      case "GET /api/v1/runs/r1/stream": {
        res.writeHead(200, { "content-type": "text/event-stream" });
        const text = [
          'id: 1\nevent: run.started\ndata: {"seq": 1, "kind": "run.started", "ts": 1, "data": {}}\n\n',
          ": keep-alive\n\n",
          'id: 2\nevent: agent.call\ndata: {"seq": 2, "kind": "agent.call", "ts": 2, "data": {"model": "m"}}\n\n',
          'event: end\ndata: {"status": "succeeded"}\n\n',
        ].join("");
        // dribble it out in 7-byte chunks so frames split mid-line
        let i = 0;
        const next = (): void => {
          if (i >= text.length) {
            res.end();
            return;
          }
          res.write(text.slice(i, i + 7));
          i += 7;
          setImmediate(next);
        };
        return next();
      }
      default:
        return json(404, { detail: "no such thing" });
    }
  });
});

let base = "";
let dir = "";
let tokenFile = "";

before(async () => {
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "cadre-ext-"));
  tokenFile = path.join(dir, "token");
  fs.writeFileSync(tokenFile, TOKEN_A + "\n");
});

after(() => {
  server.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

function client(url = () => base) {
  return new CadreClient({ baseUrl: url, readToken: () => readTokenFile(tokenFile), timeoutMs: 5000 });
}

test("health needs no token and reports the version", async () => {
  seen.length = 0;
  assert.deepEqual(await client().health(), { ok: true, version: "1.0.0" });
  assert.equal(seen[0].auth, undefined, "the token is not sent where it is not needed");
});

test("every other call carries the bearer token in a header, with a loopback Host, never in the URL", async () => {
  seen.length = 0;
  await client().orgs();
  await client().approvals();
  for (const s of seen) {
    assert.equal(s.auth, `Bearer ${TOKEN_A}`);
    assert.match(s.host ?? "", /^127\.0\.0\.1:\d+$/);
    assert.ok(!s.url.includes(TOKEN_A), "token never in a URL");
  }
  assert.equal(seen[1].url, "/api/v1/approvals?pending=true");
});

test("the token is read from the file at the moment of use", async () => {
  const c = client();
  await c.orgs();
  fs.writeFileSync(tokenFile, TOKEN_B);
  serverToken = TOKEN_B;
  try {
    await c.orgs(); // same client object, new token
  } finally {
    fs.writeFileSync(tokenFile, TOKEN_A);
    serverToken = TOKEN_A;
  }
});

test("a wrong token is a 401 ApiError whose message does not contain either token", async () => {
  serverToken = TOKEN_B;
  try {
    await assert.rejects(client().orgs(), (e: unknown) => {
      assert.ok(e instanceof ApiError);
      assert.equal(e.status, 401);
      assert.ok(!e.message.includes(TOKEN_A) && !e.message.includes(TOKEN_B));
      return true;
    });
  } finally {
    serverToken = TOKEN_A;
  }
});

test("a missing token file is TokenMissing naming the path, not a crash", async () => {
  const c = new CadreClient({ baseUrl: () => base, readToken: () => readTokenFile(path.join(dir, "nope")) });
  await assert.rejects(c.orgs(), (e: unknown) => e instanceof TokenMissing && e.message.includes(path.join(dir, "nope")));
});

test("nothing listening is ServerUnreachable; health is undefined", async () => {
  const probe = http.createServer();
  await new Promise<void>((r) => probe.listen(0, "127.0.0.1", r));
  const port = (probe.address() as AddressInfo).port;
  await new Promise<void>((r) => probe.close(() => r()));
  const c = client(() => `http://127.0.0.1:${port}`);
  assert.equal(await c.health(), undefined);
  await assert.rejects(c.orgs(), ServerUnreachable);
});

test("server error text is surfaced as the dashboard shows it", async () => {
  const c = client();
  await assert.rejects(c.startRun({ org: "o", goal: "g", project: "/dirty" }),
    (e: unknown) => e instanceof ApiError && e.status === 400 && /uncommitted changes/.test(e.message));
  await assert.rejects(c.startRun({ org: "", goal: "g" }),
    (e: unknown) => e instanceof ApiError && e.message === "org: unknown\ngoal: empty");
  await assert.rejects(c.forecast({ org: "o", goal: "" }),
    (e: unknown) => e instanceof ApiError && e.status === 422 && e.message === "Field required");
  await assert.rejects(c.decide("gone", true), (e: unknown) => e instanceof ApiError && e.status === 409);
});

test("startRun never sends allow_exec or auto_approve, whatever the caller passes", async () => {
  seen.length = 0;
  const sneaky = { org: "o", goal: "g", project: "/p", allow_exec: true, auto_approve: true } as never;
  assert.deepEqual(await client().startRun(sneaky), { id: "20260918-120000-abc123" });
  const sent = JSON.parse(seen[0].body);
  assert.deepEqual(sent, { org: "o", goal: "g", project: "/p" });
});

test("decide posts {approve, answer}", async () => {
  seen.length = 0;
  assert.deepEqual(await client().decide("a1", true, "yes"), { id: "a1", approved: true });
  assert.deepEqual(JSON.parse(seen[0].body), { approve: true, answer: "yes" });
});

test("stream: events arrive parsed and in order across split chunks, ending with `end`", async () => {
  const got: { event: string; data: unknown }[] = [];
  for await (const item of client().stream("r1", 0)) got.push(item);
  assert.deepEqual(got.map((g) => g.event), ["run.started", "agent.call", "end"]);
  assert.deepEqual(got[2].data, { status: "succeeded" });
  assert.equal((got[1].data as { seq: number }).seq, 2);
});

test("stream: aborting stops it without a ServerUnreachable", async () => {
  const ctl = new AbortController();
  ctl.abort();
  await assert.rejects(client().stream("r1", 0, ctl.signal).next(),
    (e: unknown) => e instanceof Error && e.name === "AbortError" && !(e instanceof ServerUnreachable));
});

test("detailMessage covers the shapes FastAPI returns", () => {
  assert.equal(detailMessage({ detail: "no such run" }, 404), "no such run");
  assert.equal(detailMessage({ detail: { errors: ["a", "b"] } }, 422), "a\nb");
  assert.equal(detailMessage({ detail: [{ msg: "Field required" }] }, 422), "Field required");
  assert.equal(detailMessage("host not allowed", 421), "host not allowed");
  assert.match(detailMessage(undefined, 401), /rejected the token/);
  assert.equal(detailMessage({}, 500), "HTTP 500");
});
