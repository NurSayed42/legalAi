import assert from "node:assert/strict";
import { test } from "node:test";
import { buildHistory, filtersOrNull, type Turn } from "./history.ts";

const done = (answer: object): Turn => ({ role: "ai", status: "done", answer });

test("a full turn is sent the way the old UI sent it", () => {
  const history = buildHistory([{ role: "user", text: "প্রশ্ন" }, done({ short_answer: "উত্তর" })]);
  assert.deepEqual(history, [
    { role: "user", content: "প্রশ্ন" },
    { role: "assistant", content: '{"short_answer":"উত্তর"}' },
  ]);
});

test("failed and stopped turns never reach the model", () => {
  const history = buildHistory([
    { role: "user", text: "এক" },
    { role: "ai", status: "failed" },
    { role: "user", text: "দুই" },
    { role: "ai", status: "stopped" },
    { role: "user", text: "তিন" },
    done({ short_answer: "উত্তর" }),
  ]);
  assert.deepEqual(
    history.map((m) => m.content),
    ["তিন", '{"short_answer":"উত্তর"}'],
  );
});

test("the turn being answered now isn't part of its own history", () => {
  const history = buildHistory([
    { role: "user", text: "এক" },
    { role: "ai", status: "loading" },
  ]);
  assert.deepEqual(history, []);
});

test("only the last six messages go", () => {
  const turns: Turn[] = [];
  for (let i = 0; i < 5; i += 1) turns.push({ role: "user", text: `q${i}` }, done({ short_answer: `a${i}` }));
  const history = buildHistory(turns);
  assert.equal(history.length, 6);
  assert.equal(history[0]?.content, "q2");
});

test("empty filters are null", () => {
  assert.equal(filtersOrNull({}), null);
  assert.deepEqual(filtersOrNull({ division: "HCD" }), { division: "HCD" });
});
