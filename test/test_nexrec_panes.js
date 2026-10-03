#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const uiSrc = fs.readFileSync(path.join(ROOT, "web", "public", "assets", "nexrec-ui.js"), "utf8");
const liveSrc = fs.readFileSync(path.join(ROOT, "web", "pages", "live.html"), "utf8");
const vuSrc = fs.readFileSync(path.join(ROOT, "web", "public", "assets", "nexrec-vu.js"), "utf8");
const authSrc = fs.readFileSync(path.join(ROOT, "web", "public", "assets", "nexrec-auth-gate.js"), "utf8");
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
  URLSearchParams: URLSearchParams,
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
assert.ok(exportSrc.includes("ArrowLeft"));
assert.ok(exportSrc.includes("startReverse"));
assert.ok(exportSrc.includes("0.0005"));
assert.ok(exportSrc.includes("paneInputs"));
assert.strictEqual(exportSrc.includes("inputs.slice(0, n)"), false);
assert.strictEqual(liveSrc.includes("inputs.slice(0, n)"), false);

const t0 = Date.parse("2026-09-29T03:00:00Z");
const t1 = Date.parse("2026-09-29T04:00:00Z");
const spans = sandbox.NexRecUI.coverageSpans([
  { start_at: "2026-09-29T03:20:00Z", end_at: "2026-09-29T03:25:00Z" },
  { start_at: "2026-09-29T02:00:00Z", end_at: "2026-09-29T03:10:00Z" },
  { start_at: "2026-09-29T05:00:00Z", end_at: "2026-09-29T05:05:00Z" },
], t0, t1);
assert.strictEqual(spans.length, 2);
assert.ok(Math.abs(spans[0].left - (20 / 60) * 100) < 0.01);
assert.ok(Math.abs(spans[0].width - (5 / 60) * 100) < 0.01);
assert.ok(Math.abs(spans[1].left) < 0.01);
assert.strictEqual(exportSrc.includes("coverageSpans"), false);
assert.strictEqual(exportSrc.includes("tl-thumb"), true);
assert.strictEqual(exportSrc.includes("laneColor"), true);
assert.strictEqual(UI.laneColor(0), "#3ecf8e");
assert.strictEqual(UI.laneColor(5), "#2dd4bf");
assert.strictEqual(UI.thumbRows(3600000, 1200), 1);
assert.strictEqual(UI.thumbRows(4 * 3600000, 1200), 3);
assert.strictEqual(UI.thumbRows(24 * 3600000, 800), 3);
var wrapped = UI.spansOnRows(t0 + 20 * 60000, t0 + 40 * 60000, t0, t1, 2);
assert.strictEqual(wrapped.length, 2);
assert.strictEqual(wrapped[0].row, 0);
assert.strictEqual(wrapped[1].row, 1);
assert.strictEqual(exportSrc.includes("range-start"), true);
assert.strictEqual(exportSrc.includes("No recording for "), true);
assert.ok(Number.isNaN(UI.parseStation("not a time")));
var summer = UI.parseStation("2026-07-15 15:00:00");
var winter = UI.parseStation("2026-01-15 15:00:00");
assert.strictEqual(new Date(summer).toISOString(), "2026-07-15T19:00:00.000Z");
assert.strictEqual(new Date(winter).toISOString(), "2026-01-15T20:00:00.000Z");
assert.strictEqual(UI.stationStamp(summer).indexOf("2026-07-15 15:00:00"), 0);
assert.strictEqual(authSrc.includes("stream.addTrack(ev.track)"), true);
assert.strictEqual(authSrc.includes("videoEl.srcObject = ev.streams[0]"), false);
assert.strictEqual(authSrc.includes("whepClose"), true);
assert.strictEqual(authSrc.includes("reconnecting"), true);
assert.strictEqual(authSrc.includes("framesDecoded"), true);
assert.strictEqual(authSrc.includes("visibilitychange"), true);
assert.strictEqual(authSrc.includes("pageshow"), true);
assert.strictEqual(authSrc.includes("onWake"), true);
assert.strictEqual(liveSrc.includes("nexrec-whep-track"), true);
assert.strictEqual(liveSrc.includes("nexrec-whep-state"), true);
assert.strictEqual(liveSrc.includes("whepClose"), true);
assert.strictEqual(liveSrc.includes('id="view-seg"'), true);
assert.strictEqual(liveSrc.includes('data-view="window"'), true);
assert.strictEqual(liveSrc.includes('data-view="full"'), true);
assert.strictEqual(liveSrc.includes('data-view="pip"'), true);
assert.strictEqual(liveSrc.includes("documentPictureInPicture"), true);
assert.strictEqual(liveSrc.includes("live-theater"), true);
assert.strictEqual(liveSrc.includes('id="listen-toggle"'), true);
assert.strictEqual(liveSrc.includes('id="listen-vol"'), true);
assert.strictEqual(vuSrc.includes('data-vu", "mute"'), false);
assert.strictEqual(vuSrc.includes("nexrec-vu-vol"), false);
assert.strictEqual(exportSrc.includes("paintCoverage"), true);
assert.strictEqual(exportSrc.includes("export_cancel"), true);
assert.strictEqual(exportSrc.includes("exportQueueEta"), true);

const now = Date.parse("2026-09-29T04:00:10Z");
const jobs = [
  { id: "run", status: "running", started_at: "2026-09-29T04:00:00Z", progress_pct: 50, t_in: "2026-09-29T03:00:00Z", t_out: "2026-09-29T03:01:00Z", created_at: "2026-09-29T03:59:00Z", encode_mode: "encode" },
  { id: "wait", status: "queued", t_in: "2026-09-29T03:00:00Z", t_out: "2026-09-29T03:00:08Z", created_at: "2026-09-29T03:59:30Z", quality: "full" },
];
assert.strictEqual(UI.exportRemainingMs(jobs[0], now), 10000);
assert.strictEqual(UI.exportQueueEta(jobs, jobs[1], now), 11000);
assert.strictEqual(UI.formatEta(11000), "11s");

assert.strictEqual(exportSrc.includes('id="copy-link"'), true);
assert.strictEqual(exportSrc.includes("parseExportView"), true);
assert.strictEqual(exportSrc.includes("exportViewQuery"), true);
assert.strictEqual(exportSrc.includes("exportChunkBounds"), true);
assert.strictEqual(exportSrc.includes("exportViewSlots"), true);

const playAt = Date.parse("2026-09-29T23:14:07.000Z");
const fromAt = Date.parse("2026-09-29T22:00:00.000Z");
const toAt = Date.parse("2026-09-29T23:59:00.000Z");
const inAt = Date.parse("2026-09-29T23:10:00.000Z");
const outAt = Date.parse("2026-09-29T23:12:30.000Z");
const shared = UI.exportViewQuery({
  n: 4,
  ids: ["in_a", "", "in_c", "in_b"],
  sel: 2,
  t: playAt,
  from: fromAt,
  to: toAt,
  markIn: inAt,
  markOut: outAt,
});
const opened = UI.parseExportView("?" + shared);
assert.strictEqual(opened.t, playAt);
assert.strictEqual(opened.n, 4);
same(opened.ids, ["in_a", "", "in_c", "in_b"]);
assert.strictEqual(UI.exportViewLayout(opened), 4);
same(UI.exportViewSlots(opened), ["in_a", "", "in_c", "in_b", "", ""]);
assert.strictEqual(UI.exportViewSelection(opened), 2);
assert.strictEqual(opened.from, fromAt);
assert.strictEqual(opened.to, toAt);
assert.strictEqual(opened.markIn, inAt);
assert.strictEqual(opened.markOut, outAt);

const asrun = UI.parseExportView("?inputs=in_a,in_b&t=2026-09-29T23:14:07Z");
assert.strictEqual(asrun.n, null);
assert.strictEqual(UI.exportViewLayout(asrun), 2);
same(asrun.ids, ["in_a", "in_b"]);
assert.strictEqual(asrun.from, null);
assert.strictEqual(asrun.markIn, null);
assert.strictEqual(asrun.sel, null);

const timeOnly = UI.parseExportView("?t=2026-09-29T23:14:07Z");
assert.strictEqual(timeOnly.ids, null);
assert.strictEqual(timeOnly.n, null);

const wide = UI.exportChunkBounds({
  t: playAt,
  from: playAt - 5 * 3600000,
  to: playAt + 4 * 3600000,
});
assert.strictEqual(wide.from, playAt - 5 * 3600000);
assert.strictEqual(wide.to, playAt + 4 * 3600000);
const tight = UI.exportChunkBounds({ t: playAt, from: playAt - 60000, to: playAt + 60000 });
assert.strictEqual(tight.from, playAt - 3 * 3600000);
assert.strictEqual(tight.to, playAt + 3 * 3600000);

const unmarked = UI.parseExportView("?" + UI.exportViewQuery({
  n: 1, ids: ["in_a"], sel: 0, t: playAt, from: fromAt, to: toAt, markIn: null, markOut: null,
}));
assert.strictEqual(unmarked.markIn, null);
assert.strictEqual(unmarked.markOut, null);
assert.strictEqual(UI.exportViewSelection({ n: 2, sel: 9, ids: ["a", "b"] }), 0);

console.log("pane assignment ok");
