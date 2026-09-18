import assert from "node:assert/strict";
import { test } from "node:test";
import { SseParser } from "../../src/sse";

const FRAME = 'id: 7\nevent: agent.call\ndata: {"seq": 7, "kind": "agent.call"}\n\n';

test("parses a whole frame", () => {
  const p = new SseParser();
  assert.deepEqual(p.push(FRAME), [{ event: "agent.call", data: '{"seq": 7, "kind": "agent.call"}', id: "7" }]);
});

test("a frame split at every possible byte comes out once and intact", () => {
  for (let cut = 1; cut < FRAME.length; cut++) {
    const p = new SseParser();
    const got = [...p.push(FRAME.slice(0, cut)), ...p.push(FRAME.slice(cut))];
    assert.equal(got.length, 1, `cut at ${cut}`);
    assert.equal(got[0].event, "agent.call");
  }
});

test("CRLF framing, including a CR/LF pair split across chunks", () => {
  const crlf = FRAME.replace(/\n/g, "\r\n");
  const at = crlf.indexOf("\r\n\r\n") + 1; // between \r and \n
  const p = new SseParser();
  const got = [...p.push(crlf.slice(0, at)), ...p.push(crlf.slice(at))];
  assert.equal(got.length, 1);
  assert.equal(got[0].data, '{"seq": 7, "kind": "agent.call"}');
});

test("keep-alive comments and data-less blocks are skipped; default event is message", () => {
  const p = new SseParser();
  const got = p.push(": keep-alive\n\nevent: nothing\n\ndata: a\ndata: b\n\n");
  assert.deepEqual(got, [{ event: "message", data: "a\nb", id: undefined }]);
});

test("the end frame the server sends", () => {
  const p = new SseParser();
  assert.deepEqual(p.push('event: end\ndata: {"status": "succeeded"}\n\n'),
    [{ event: "end", data: '{"status": "succeeded"}', id: undefined }]);
});

test("several frames in one chunk; a partial one waits", () => {
  const p = new SseParser();
  const got = p.push(FRAME + FRAME + "event: x\ndata: 1");
  assert.equal(got.length, 2);
  assert.deepEqual(p.push("\n\n"), [{ event: "x", data: "1", id: undefined }]);
});
