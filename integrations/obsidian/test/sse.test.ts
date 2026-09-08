import assert from "node:assert/strict";
import test from "node:test";

import { SseSplitter, parseFrame, parseVerdict } from "../src/sse.ts";

test("splits frames across chunk boundaries", () => {
  const splitter = new SseSplitter();
  assert.deepEqual(splitter.push('event: meta\ndata: {"request_id":"a"}'), []);
  const frames = splitter.push('\n\nevent: token\ndata: {"text":"He');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "meta");
  assert.equal(frames[0].data.request_id, "a");
  const rest = splitter.push('llo"}\n\n');
  assert.equal(rest[0].event, "token");
  assert.equal(rest[0].data.text, "Hello");
});

test("handles CRLF, heartbeats, and multi-line data", () => {
  const splitter = new SseSplitter();
  const frames = splitter.push(
    ': keep-alive\r\n\r\nevent: token\r\ndata: {"text":"a"}\r\n\r\ndata: {"text":"b"}\r\n\r\n',
  );
  assert.deepEqual(
    frames.map((f) => [f.event, f.data.text]),
    [
      ["token", "a"],
      ["message", "b"],
    ],
  );
});

test("tolerates malformed JSON and empty frames", () => {
  assert.deepEqual(parseFrame("event: token\ndata: {oops")?.data, {});
  assert.equal(parseFrame(": just a comment"), null);
  assert.equal(parseFrame(""), null);
  assert.deepEqual(parseFrame("event: done\ndata: [1,2]")?.data, {});
});

test("flush returns a trailing frame without a blank line", () => {
  const splitter = new SseSplitter();
  splitter.push('event: done\ndata: {"elapsed_ms":12}');
  const flushed = splitter.flush();
  assert.equal(flushed[0].event, "done");
  assert.equal(flushed[0].data.elapsed_ms, 12);
  assert.deepEqual(splitter.flush(), []);
});

test("parses a prove-it verdict tolerantly", () => {
  assert.equal(parseVerdict("Supported. The passage matches src/app.py:10."), "Supported");
  assert.equal(parseVerdict("**Partially supported** — only half."), "Partially supported");
  assert.equal(parseVerdict("## Verdict: Not supported\n\nBecause…"), "Not supported");
  assert.equal(parseVerdict("no evidence found in the folder"), "No evidence found");
  assert.equal(parseVerdict("\n\nThe passage is broadly right."), null);
  assert.equal(parseVerdict(""), null);
});
