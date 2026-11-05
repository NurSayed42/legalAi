import assert from "node:assert/strict";
import { test } from "node:test";
import {
  bnDigits,
  bnNumber,
  highlightSegments,
  looksEnglish,
  parseDocument,
  parseInline,
  relativeToTop,
  safeUrl,
  stripMarkdown,
} from "./format.ts";

test("numbers read as Bangla", () => {
  assert.equal(bnNumber(8615), "৮,৬১৫");
  assert.equal(bnNumber(1399), "১,৩৯৯");
  assert.equal(bnDigits(2024), "২০২৪");
});

test("only web links survive, whatever a retrieved text claims", () => {
  assert.equal(safeUrl("http://bdlaws.minlaw.gov.bd/act-46.html"), "http://bdlaws.minlaw.gov.bd/act-46.html");
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("data:text/html,x"), null);
  assert.equal(safeUrl("not a url"), null);
  assert.equal(safeUrl(null), null);
});

test("highlight marks query terms and keeps all the text", () => {
  const text = "Section 138 of the Negotiable Instruments Act";
  const parts = highlightSegments(text, "section 138");
  assert.equal(parts.map((p) => p.text).join(""), text);
  assert.deepEqual(
    parts.filter((p) => p.hit).map((p) => p.text),
    ["Section", "138"],
  );
});

test("highlight treats regex characters in the query as text", () => {
  const parts = highlightSegments("a (b) c+d", "(b) c+d");
  assert.equal(parts.map((p) => p.text).join(""), "a (b) c+d");
  assert.deepEqual(
    parts.filter((p) => p.hit).map((p) => p.text),
    ["c+d"],
  );
});

test("highlight works on Bangla and skips stopwords", () => {
  const parts = highlightSegments("চেক ডিজঅনার ও নোটিশ", "চেক ও নোটিশ");
  assert.deepEqual(
    parts.filter((p) => p.hit).map((p) => p.text),
    ["চেক", "নোটিশ"],
  );
});

test("every adjacent hit is marked, not just alternate ones", () => {
  const parts = highlightSegments("cheque cheque cheque", "cheque");
  assert.equal(parts.filter((p) => p.hit).length, 3);
});

test("relevance bar is relative to the top result, never a raw percentage", () => {
  assert.equal(relativeToTop(0.04, 0.04), 1);
  assert.equal(relativeToTop(0.02, 0.04), 0.5);
  assert.equal(relativeToTop(0, 0.04), 0);
  assert.equal(relativeToTop(0.0001, 0.04), 0.08);
});

test("document parser handles the shapes the generator returns", () => {
  const blocks = parseDocument(
    "**Title**\n\n---\n\n### ১. পক্ষগণ\n\n- এক\n- দুই\n\n1. প্রথম\n২. দ্বিতীয়\n\nশেষ লাইন\nআরেক লাইন",
  );
  assert.deepEqual(
    blocks.map((b) => b.type),
    ["heading", "rule", "heading", "list", "list", "paragraph"],
  );
  assert.ok(blocks[0]?.type === "heading" && blocks[0].level === 1, "a bold first line is the title");
  const ordered = blocks[4];
  assert.ok(ordered?.type === "list" && ordered.ordered && ordered.items.length === 2);
});

test("inline bold is split out", () => {
  assert.deepEqual(parseInline("a **b** c"), [
    { text: "a ", strong: false },
    { text: "b", strong: true },
    { text: " c", strong: false },
  ]);
});

test("plain text drops Markdown symbols and keeps signature blanks", () => {
  assert.equal(
    stripMarkdown("### শিরোনাম\n\n**মালিক:** ক\n---\n\nস্বাক্ষর: ______"),
    "শিরোনাম\n\nমালিক: ক\n\nস্বাক্ষর: ______",
  );
});

test("language guess", () => {
  assert.equal(looksEnglish("Section 138 of the Act"), true);
  assert.equal(looksEnglish("ধারা ১৩৮ Act"), false);
});
