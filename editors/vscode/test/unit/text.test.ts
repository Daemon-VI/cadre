import assert from "node:assert/strict";
import { test } from "node:test";
import { inert, notificationText } from "../../src/text";

// VS Code's notification link syntax: [label](target), where target may be command:…
const LINK = /\[([^\]]+)\]\(((?:https?:\/\/|command:|file:)[^)\s]+)\)/;

test("markdown links in model text become inert", () => {
  const evil = "Proceed? [Yes](command:workbench.action.terminal.sendSequence?%7B%22text%22%3A%22x%22%7D)";
  assert.ok(LINK.test(evil));
  const safe = inert(evil);
  assert.ok(!LINK.test(safe));
  assert.ok(safe.includes("Yes"), "the words stay readable");
  assert.ok(!LINK.test(inert("[a]\n(https://example.com)")));
});

test("notification text is flattened, inert and bounded", () => {
  const t = notificationText("line one\n  line two [x](https://e.com)\n" + "y".repeat(1000), 120);
  assert.ok(!t.includes("\n"));
  assert.ok(!LINK.test(t));
  assert.equal(t.length, 120);
  assert.ok(t.endsWith("…"));
});
