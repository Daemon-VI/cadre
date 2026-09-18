import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import {
  cadreHome, cliInvocation, describeInvocation, envGet, findOnPath, isPresetId, serverInvocation, tokenPath, validPort,
} from "../../src/cli";

test("server command: cadre on PATH wins", () => {
  const which = (n: string) => ({ cadre: "/usr/bin/cadre", uvx: "/usr/bin/uvx" } as Record<string, string>)[n];
  assert.deepEqual(serverInvocation(8765, which), { command: "/usr/bin/cadre", args: ["serve", "--port", "8765"], via: "cadre" });
});

test("server command: uvx cadre-ai when cadre is missing", () => {
  const which = (n: string) => (n === "uvx" ? "C:\\uv\\uvx.exe" : undefined);
  const inv = serverInvocation(8799, which);
  assert.deepEqual(inv, { command: "C:\\uv\\uvx.exe", args: ["cadre-ai", "serve", "--port", "8799"], via: "uvx" });
  assert.equal(describeInvocation(inv!), "uvx cadre-ai serve --port 8799");
});

test("server command: nothing when neither is on PATH", () => {
  assert.equal(serverInvocation(8765, () => undefined), undefined);
});

test("provider add goes to the CLI, never with a key argument", () => {
  const inv = cliInvocation(["provider", "add", "groq"], (n) => (n === "cadre" ? "/bin/cadre" : undefined));
  assert.deepEqual(inv?.args, ["provider", "add", "groq"]);
});

test("findOnPath: POSIX search order and no extension", () => {
  const seen: string[] = [];
  const exists = (p: string) => {
    seen.push(p);
    return p === "/opt/b/cadre";
  };
  const got = findOnPath("cadre", { PATH: "/opt/a::/opt/b" }, "linux", exists);
  assert.equal(got, "/opt/b/cadre");
  assert.deepEqual(seen, ["/opt/a/cadre", "/opt/b/cadre"]);
});

test("findOnPath: Windows looks for .exe/.com only, reads Path case-insensitively, strips quotes", () => {
  const seen: string[] = [];
  const exists = (p: string) => {
    seen.push(p);
    return p === path.win32.join("C:\\Tools uv", "uvx.exe");
  };
  const got = findOnPath("uvx", { Path: "C:\\bin;\"C:\\Tools uv\"" }, "win32", exists);
  assert.equal(got, "C:\\Tools uv\\uvx.exe");
  assert.ok(seen.every((p) => /\.(exe|com)$/.test(p)), "never a .cmd or .bat, which would need a shell");
});

test("envGet is case-insensitive only on Windows", () => {
  assert.equal(envGet({ Cadre_Home: "x" }, "CADRE_HOME", "win32"), "x");
  assert.equal(envGet({ Cadre_Home: "x" }, "CADRE_HOME", "linux"), undefined);
});

test("CADRE_HOME overrides ~/.cadre, as the engine does", () => {
  assert.equal(cadreHome({}, "/home/r"), path.join("/home/r", ".cadre"));
  assert.equal(cadreHome({ CADRE_HOME: "" }, "/home/r"), path.join("/home/r", ".cadre"));
  assert.equal(cadreHome({ CADRE_HOME: "/tmp/ch" }, "/home/r"), "/tmp/ch");
  assert.equal(tokenPath({ CADRE_HOME: "/tmp/ch" }, "/home/r"), path.join("/tmp/ch", "token"));
});

test("validPort", () => {
  assert.equal(validPort(8765), 8765);
  assert.equal(validPort("8799"), 8799);
  for (const bad of [0, 65536, 1.5, "x", null, undefined, -1]) assert.equal(validPort(bad), undefined);
});

test("preset ids are slugs; anything shell-like is refused", () => {
  for (const ok of ["groq", "gemini", "open-router", "cf_ai"]) assert.ok(isPresetId(ok), ok);
  for (const bad of ["", "Groq", "groq; rm -rf ~", "--key=x", "a b", "$(x)", "x".repeat(40)]) assert.ok(!isPresetId(bad), bad);
});
