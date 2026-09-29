#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const uiSrc = fs.readFileSync(path.join(ROOT, "web", "public", "assets", "nexrec-ui.js"), "utf8");
const liveSrc = fs.readFileSync(path.join(ROOT, "web", "pages", "live.html"), "utf8");
const exportSrc = fs.readFileSync(path.join(ROOT, "web", "pages", "export.html"), "utf8");

const store = new Map();
const sandbox = {
  window: null,
  document: { documentElement: { setAttribute() {}, getAttribute() { return "dark"; } }, getElementById() { return null; }, readyState: "complete", addEventListener() {} },
  localStorage: {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(k, String(v)); },
  },
  fetch() { return Promise.resolve({ json() { return {}; } }); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(uiSrc, sandbox);
const UI = sandbox.NexRecUI;

const inputs = [
  { id: "a", name: "Alpha" },
  { id: "b", name: "Bravo" },
  { id: "c", name: "Charlie" },
  { id: "d", name: "Delta" },
  { id: "e", name: "Echo" },
];

function same(actual, expected) {
  assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected));
}

const fresh = UI.paneSlotIds(inputs, null);
same(fresh, ["a", "b", "c", "d", "e", ""]);
same(
  UI.paneInputs(inputs, fresh, 2).map((row) => row && row.id),
  ["a", "b"]
);

const swapped = UI.paneAssign(fresh, 0, "e");
same(swapped, ["e", "b", "c", "d", "a", ""]);

const cleared = UI.paneAssign(swapped, 1, "");
assert.strictEqual(cleared[1], "");
assert.strictEqual(cleared[0], "e");

const dropped = UI.paneSlotIds(inputs, ["missing", "c", "c", "", "b"]);
same(dropped, ["", "c", "", "", "b", ""]);

UI.writePaneSlots(sandbox.localStorage, "nexrec-live-slots", swapped);
same(UI.readPaneSlots(sandbox.localStorage, "nexrec-live-slots"), swapped);
assert.strictEqual(UI.readPaneSlots(sandbox.localStorage, "nexrec-export-slots"), null);

assert.ok(liveSrc.includes('aria-label", "Input for pane "'));
assert.ok(liveSrc.includes("nexrec-live-slots"));
assert.ok(liveSrc.includes("paneAssign"));
assert.ok(exportSrc.includes("nexrec-export-slots"));
assert.ok(exportSrc.includes("paneAssign"));
assert.ok(exportSrc.includes("paneInputs"));
assert.strictEqual(exportSrc.includes("inputs.slice(0, n)"), false);
assert.strictEqual(liveSrc.includes("inputs.slice(0, n)"), false);

console.log("pane assignment ok");
