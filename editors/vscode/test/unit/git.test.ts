// Review branch's git plumbing, against a throwaway repository shaped like a project-mode run:
// a base commit, then `cadre/<run-id>` with an added, a modified and a deleted file.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, test } from "node:test";
import { changedFiles, fileAt, isSafeRef, parseNameStatusZ } from "../../src/git";

let repo = "";
let base = "";
const BRANCH = "cadre/20260918-120000-abc123";

function g(...args: string[]): string {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8" }).trim();
}

before(() => {
  repo = fs.mkdtempSync(path.join(os.tmpdir(), "cadre-git-"));
  g("init", "-q", "-b", "main");
  g("config", "user.name", "Test");
  g("config", "user.email", "test@example.invalid");
  g("config", "commit.gpgsign", "false");
  fs.writeFileSync(path.join(repo, "keep.txt"), "one\n");
  fs.writeFileSync(path.join(repo, "gone.txt"), "bye\n");
  fs.mkdirSync(path.join(repo, "dir with space"));
  fs.writeFileSync(path.join(repo, "dir with space", "é.txt"), "old\n");
  g("add", "-A");
  g("commit", "-q", "-m", "base");
  base = g("rev-parse", "HEAD");
  g("checkout", "-q", "-b", BRANCH);
  fs.writeFileSync(path.join(repo, "dir with space", "é.txt"), "new\n");
  fs.rmSync(path.join(repo, "gone.txt"));
  fs.writeFileSync(path.join(repo, "added.py"), "print('hi')\n");
  g("add", "-A");
  g("commit", "-q", "-m", "run work");
  g("checkout", "-q", "main");
});

after(() => fs.rmSync(repo, { recursive: true, force: true }));

test("changedFiles lists added, modified and deleted paths, odd names intact", async () => {
  const files = await changedFiles(repo, base, BRANCH);
  const byPath = Object.fromEntries(files.map((f) => [f.path, f.status]));
  assert.deepEqual(byPath, { "added.py": "A", "dir with space/é.txt": "M", "gone.txt": "D" });
});

test("fileAt reads each side; a missing side is undefined", async () => {
  assert.equal(await fileAt(repo, base, "dir with space/é.txt"), "old\n");
  assert.equal(await fileAt(repo, BRANCH, "dir with space/é.txt"), "new\n");
  assert.equal(await fileAt(repo, base, "added.py"), undefined);
  assert.equal(await fileAt(repo, BRANCH, "gone.txt"), undefined);
});

test("option-like or odd refs are refused before git sees them", async () => {
  for (const bad of ["--output=/tmp/x", "-x", "a..b", "", "x y", "HEAD~1;rm", "a.lock"]) assert.ok(!isSafeRef(bad), bad);
  for (const ok of [BRANCH, "0123456789abcdef0123456789abcdef01234567", "main"]) assert.ok(isSafeRef(ok), ok);
  await assert.rejects(changedFiles(repo, "--output=x", BRANCH), /unusual ref/);
});

test("parseNameStatusZ", () => {
  assert.deepEqual(parseNameStatusZ("M\0a.txt\0A\0b c.txt\0"), [{ status: "M", path: "a.txt" }, { status: "A", path: "b c.txt" }]);
  assert.deepEqual(parseNameStatusZ("R100\0old\0new\0D\0x\0"), [{ status: "R", path: "new" }, { status: "D", path: "x" }]);
  assert.deepEqual(parseNameStatusZ(""), []);
});
