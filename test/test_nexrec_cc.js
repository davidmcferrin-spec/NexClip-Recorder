#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const ASSETS = path.join(ROOT, "web", "public", "assets");

function makeEl(tag) {
  const el = {
    tagName: String(tag || "").toUpperCase(),
    className: "",
    hidden: false,
    textContent: "",
    children: [],
    parentNode: null,
  };
  el.appendChild = function (child) {
    el.children.push(child);
    child.parentNode = el;
    return child;
  };
  el.removeChild = function (child) {
    el.children = el.children.filter((c) => c !== child);
    if (child.parentNode === el) child.parentNode = null;
    return child;
  };
  return el;
}

const store = new Map();
const sandbox = {
  window: {},
  document: { createElement: makeEl },
  localStorage: {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
  },
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(ASSETS, "nexrec-cc.js"), "utf8"), sandbox, {
  filename: "nexrec-cc.js",
});

const cc = sandbox.NexRecCc;
const t0 = Date.parse("2026-09-29T15:00:00Z");
const cues = [
  { t_start: "2026-09-29T15:00:00Z", t_end: "2026-09-29T15:00:02Z", text: "Hello" },
  { t_start: "2026-09-29T15:00:02Z", t_end: "2026-09-29T15:00:04Z", text: "There" },
  { t_start: "2026-09-29T15:00:10Z", text: "Hold" },
];

function lines(cues, ms) {
  return Array.from(cc.linesAt(cues, ms), String);
}
assert.deepStrictEqual(lines(cues, t0), ["Hello"]);
assert.deepStrictEqual(lines(cues, t0 + 1999), ["Hello"]);
assert.deepStrictEqual(lines(cues, t0 + 2000), ["There"]);
assert.deepStrictEqual(lines(cues, t0 + 5000), []);
assert.deepStrictEqual(lines(cues, t0 + 10000), ["Hold"]);
assert.deepStrictEqual(lines(cues, t0 + 12000), []);
assert.deepStrictEqual(lines([{ t_start: "nope", text: "x" }], t0), []);

const pane = makeEl("div");
const box = cc.attach({ container: pane });
assert.strictEqual(box.isVisible(), false);
box.setCues(cues);
box.setTime(t0);
assert.strictEqual(pane.children[0].hidden, true);
box.setVisible(true);
assert.strictEqual(pane.children[0].hidden, false);
assert.strictEqual(pane.children[0].children[0].textContent, "Hello");
box.setTime(t0 + 5000);
assert.strictEqual(pane.children[0].hidden, true);
assert.strictEqual(pane.children[0].children[0].textContent, "");
box.setVisible(false);
assert.strictEqual(cc.getOnPref(), false);
box.destroy();
assert.strictEqual(pane.children.length, 0);

const live = fs.readFileSync(path.join(ROOT, "web", "pages", "live.html"), "utf8");
const exp = fs.readFileSync(path.join(ROOT, "web", "pages", "export.html"), "utf8");
const api = fs.readFileSync(path.join(ROOT, "web", "nexrec-api.php"), "utf8");
for (const html of [live, exp]) {
  assert.ok(html.includes("/assets/nexrec-cc.js"));
  assert.ok(html.includes('id="cc-toggle"'));
  assert.ok(html.includes("captions_window"));
}
assert.ok(api.includes("captions_window"));
assert.ok(api.includes("kind = :k"));
assert.ok(!api.includes('kind = "caption"'));

console.log("nexrec cc ok");
