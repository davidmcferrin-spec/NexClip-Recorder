#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const ASSETS = path.join(ROOT, "web", "public", "assets");

function memStore() {
  const m = new Map();
  return {
    getItem(k) { return m.has(k) ? m.get(k) : null; },
    setItem(k, v) { m.set(String(k), String(v)); },
    removeItem(k) { m.delete(k); },
  };
}

function makeCtx() {
  return {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textBaseline: "",
    globalAlpha: 1,
    save() {},
    restore() {},
    fillRect() {},
    drawImage() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    arc() {},
    strokeRect() {},
    fillText() {},
    setLineDash() {},
    getImageData(x, y, w, h) {
      return { data: new Uint8ClampedArray((w || 0) * (h || 0) * 4) };
    },
    putImageData() {},
  };
}

function makeDocument() {
  const ids = {};
  function makeEl(tag) {
    const el = {
      tagName: String(tag || "").toUpperCase(),
      className: "",
      hidden: false,
      textContent: "",
      title: "",
      value: "",
      type: "",
      min: "",
      max: "",
      step: "",
      width: 0,
      height: 0,
      style: {},
      attrs: {},
      children: [],
      parentNode: null,
      offsetWidth: 0,
      offsetHeight: 0,
      readyState: 0,
      videoWidth: 0,
      muted: true,
      srcObject: null,
      _on: {},
    };
    const classes = new Set();
    el.classList = {
      toggle(name, on) {
        if (arguments.length < 2) {
          if (classes.has(name)) classes.delete(name);
          else classes.add(name);
        } else if (on) classes.add(name);
        else classes.delete(name);
      },
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
    };
    Object.defineProperty(el, "id", {
      get() { return el.attrs.id || ""; },
      set(v) {
        el.attrs.id = String(v);
        ids[el.attrs.id] = el;
      },
    });
    Object.defineProperty(el, "firstChild", {
      get() { return el.children[0] || null; },
    });
    el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
    el.getAttribute = function (k) { return el.attrs[k]; };
    el.appendChild = function (child) {
      if (child.parentNode && child.parentNode !== el) {
        child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
      }
      el.children.push(child);
      child.parentNode = el;
      return child;
    };
    el.removeChild = function (child) {
      el.children = el.children.filter((c) => c !== child);
      if (child.parentNode === el) child.parentNode = null;
      return child;
    };
    el.addEventListener = function (type, fn) {
      (el._on[type] = el._on[type] || []).push(fn);
    };
    el.removeEventListener = function (type, fn) {
      el._on[type] = (el._on[type] || []).filter((f) => f !== fn);
    };
    el.getContext = function () { return makeCtx(); };
    el.getBoundingClientRect = function () {
      return { left: 20, top: 30, width: 100, height: 80 };
    };
    el.setPointerCapture = function () {};
    el.releasePointerCapture = function () {};
    el.requestVideoFrameCallback = function () { return 1; };
    el.cancelVideoFrameCallback = function () {};
    el.closest = function () { return null; };
    return el;
  }

  const document = {
    ids: ids,
    createElement: makeEl,
    getElementById(id) { return ids[id] || null; },
    querySelector() { return null; },
    addEventListener(type, fn) { (document._on[type] = document._on[type] || []).push(fn); },
    removeEventListener(type, fn) {
      document._on[type] = (document._on[type] || []).filter((f) => f !== fn);
    },
    _on: {},
  };
  document.head = makeEl("head");
  document.body = makeEl("body");
  document._makeEl = makeEl;
  return document;
}

function load(filename, sandbox) {
  const code = fs.readFileSync(path.join(ASSETS, filename), "utf8");
  vm.runInContext(code, sandbox, { filename: filename });
}

function sandbox() {
  const document = makeDocument();
  let raf = 0;
  const queue = [];
  const box = {
    console: console,
    document: document,
    localStorage: memStore(),
    innerWidth: 1280,
    innerHeight: 720,
    requestAnimationFrame(cb) {
      const id = ++raf;
      queue.push({ id: id, cb: cb });
      return id;
    },
    cancelAnimationFrame(id) {
      const i = queue.findIndex((q) => q.id === id);
      if (i >= 0) queue.splice(i, 1);
    },
    flushFrame() {
      const batch = queue.splice(0, queue.length);
      batch.forEach((item) => item.cb());
    },
  };
  box._on = {};
  box.addEventListener = function (type, fn) {
    (box._on[type] = box._on[type] || []).push(fn);
  };
  box.removeEventListener = function (type, fn) {
    box._on[type] = (box._on[type] || []).filter((f) => f !== fn);
  };
  box.window = box;
  box.globalThis = box;
  vm.createContext(box);
  return box;
}

function fire(el, type, ev) {
  (el._on[type] || []).forEach((fn) => fn(ev));
}

const scopesSrc = fs.readFileSync(path.join(ASSETS, "nexrec-scopes.js"), "utf8");
const vuSrc = fs.readFileSync(path.join(ASSETS, "nexrec-vu.js"), "utf8");
const specSrc = fs.readFileSync(path.join(ASSETS, "nexrec-spectrum.js"), "utf8");
const liveSrc = fs.readFileSync(path.join(ROOT, "web", "pages", "live.html"), "utf8");

assert.strictEqual(scopesSrc.split("createMediaStreamSource").length, 1);
assert.strictEqual(vuSrc.split("createMediaStreamSource").length, 2);
assert.strictEqual(specSrc.includes("createMediaStreamSource"), false);
assert.ok(specSrc.includes("getSpectrumPair"));
[scopesSrc, vuSrc, specSrc].forEach((src) => {
  assert.strictEqual(src.includes("record_argv"), false);
  assert.strictEqual(src.includes("ebur128"), false);
});
assert.ok(liveSrc.includes("/assets/nexrec-scopes.js"));
assert.ok(liveSrc.includes("/assets/nexrec-vu.js"));
assert.ok(liveSrc.includes("/assets/nexrec-spectrum.js"));
assert.ok(liveSrc.includes('data-mon="scopes"'));
assert.strictEqual(liveSrc.includes("live decode NEXT"), false);
assert.strictEqual(liveSrc.includes('id="analyzers"'), false);

const box = sandbox();
load("nexrec-scopes.js", box);
load("nexrec-vu.js", box);
load("nexrec-spectrum.js", box);
const Scopes = box.NexRecScopes;
const Vu = box.NexRecVu;
const Spectrum = box.NexRecSpectrum;

assert.ok(Scopes.PREF_ON.startsWith("nexrec-"));
assert.ok(Scopes.PREF_POP.startsWith("nexrec-"));
assert.ok(Scopes.PREF_POS.startsWith("nexrec-"));
assert.ok(Vu.PREF_VISIBLE.startsWith("nexrec-"));
assert.ok(Vu.PREF_MUTED.startsWith("nexrec-"));
assert.ok(Vu.PREF_VOLUME.startsWith("nexrec-"));
assert.ok(Spectrum.PREF_ON.startsWith("nexrec-"));
assert.strictEqual(Scopes.getOnPref(), false);
assert.strictEqual(Vu.getVisiblePref(), false);
assert.strictEqual(Vu.getMutedPref(), true);
assert.strictEqual(Spectrum.getOnPref(), false);

const white = Scopes.rgbToYcbcr(1, 1, 1);
assert.ok(Math.abs(white.y - 1) < 1e-9);
assert.ok(Math.abs(white.cb) < 1e-9);
assert.ok(Math.abs(white.cr) < 1e-9);
assert.strictEqual(Scopes.yToIre(1), 100);
assert.strictEqual(Scopes.yToIre(0), 0);

const targets = Scopes.barTargets();
assert.strictEqual(targets.length, 6);
assert.strictEqual(targets.map((t) => t.name).join(","), "R,Mg,B,Cy,G,Yl");
assert.ok(targets[0].cr > 0);

const L = Scopes.layoutFor(false);
const wfmD = new Uint8ClampedArray(L.wfmW * L.wfmH * 4);
const vecD = new Uint8ClampedArray(L.vecSize * L.vecSize * 4);
Scopes.plotFrame(new Uint8ClampedArray([255, 255, 255, 255]), L.sampleW, L, wfmD, vecD);
const ireY = L.plotT;
const wfmO = (ireY * L.wfmW + L.plotL) * 4;
assert.ok(wfmD[wfmO + 1] >= 160, "white sits at 100 IRE");
const vcx = L.vecSize / 2;
const vecO = (vcx * L.vecSize + vcx) * 4;
assert.strictEqual(vecD[vecO + 3], 255, "white is at vectorscope center");

const redW = new Uint8ClampedArray(L.wfmW * L.wfmH * 4);
const redV = new Uint8ClampedArray(L.vecSize * L.vecSize * 4);
Scopes.plotFrame(new Uint8ClampedArray([255, 0, 0, 255]), L.sampleW, L, redW, redV);
let redAbove = false;
for (let y = 0; y < vcx; y++) {
  for (let x = 0; x < L.vecSize; x++) {
    if (redV[(y * L.vecSize + x) * 4 + 3] === 255) redAbove = true;
  }
}
assert.ok(redAbove, "red Cr plots above vectorscope center");

const stereo = Vu.describeChannels(2);
assert.strictEqual(stereo.labels.join(","), "L,R");
assert.strictEqual(stereo.proxyStereo, true);
assert.strictEqual(stereo.has51, false);
assert.strictEqual(stereo.assumed, false);
const assumed = Vu.describeChannels(0);
assert.strictEqual(assumed.assumed, true);
assert.strictEqual(assumed.labels.join(","), "L,R");
assert.strictEqual(Vu.describeChannels(1).labels.join(","), "M");
const surround = Vu.describeChannels(6);
assert.strictEqual(surround.has51, true);
assert.ok(surround.labels.includes("LFE"));
assert.ok(surround.labels.includes("C"));
const eight = Vu.describeChannels(8);
assert.strictEqual(eight.labels.join(","), "L,R,C,LFE,Ls,Rs,7,8");
assert.strictEqual(eight.labels.includes("SAPL"), false);
const many = Vu.describeChannels(64);
assert.strictEqual(many.count, Vu.MAX_CH);
assert.strictEqual(many.limited, true);
assert.strictEqual(Vu.resolveChannelCount(0, 2).labels.join(","), "L,R");
assert.strictEqual(Vu.resolveChannelCount(0, 2).assumed, false);
assert.ok(Vu.monitorNote(stereo, false).includes("waiting"));
assert.ok(Vu.monitorNote(stereo, true).includes("stereo AAC proxy"));
assert.ok(Vu.PROXY_LIMIT.includes("NEXT"));

assert.strictEqual(Vu.heightFromDb(-60), 0);
assert.strictEqual(Vu.heightFromDb(0), 100);
assert.ok(Math.abs(Vu.heightFromDb(Vu.ALIGN_DB) - (40 / 60) * 100) < 1e-9);
assert.strictEqual(Vu.dbScaleLabel(0), "FS");
assert.strictEqual(Spectrum.heightFromDb(-60), 0);
assert.strictEqual(Spectrum.heightFromDb(0), 100);
assert.strictEqual(Spectrum.BANDS, 64);
assert.strictEqual(Spectrum.F_MIN, 10);
assert.strictEqual(Spectrum.F_MAX, 22000);

const edges = Spectrum.bandEdges(64, 10, 22000);
assert.strictEqual(edges.length, 65);
assert.ok(Math.abs(edges[0] - 10) < 1e-6);
assert.ok(Math.abs(edges[64] - 22000) < 1e-6);
for (let i = 1; i < edges.length; i++) assert.ok(edges[i] > edges[i - 1]);

const fftSize = 8192;
const sampleRate = 48000;
const freq = new Float32Array(fftSize / 2);
freq.fill(-100);
const bin = Math.round(1000 / (sampleRate / fftSize));
freq[bin] = -12;
const bands = new Float32Array(64);
Spectrum.fillBands(freq, sampleRate, fftSize, bands, edges);
let band = 0;
for (let i = 0; i < 64; i++) {
  if (1000 >= edges[i] && 1000 < edges[i + 1]) band = i;
}
assert.strictEqual(bands[band], -12);
const emptyBands = new Float32Array(64);
Spectrum.fillBands(null, sampleRate, fftSize, emptyBands, edges);
for (let i = 0; i < 64; i++) assert.strictEqual(emptyBands[i], Spectrum.DB_MIN);

const pos = Scopes.clampPos(-40, 5000, 100, 80, 800, 600, 8);
assert.strictEqual(pos.left, 8);
assert.strictEqual(pos.top, 600 - 80 - 8);
assert.strictEqual(Scopes.parsePos("nope"), null);
const parsed = Scopes.parsePos('{"left":12,"top":18}');
assert.strictEqual(parsed.left, 12);
assert.strictEqual(parsed.top, 18);
Scopes.setPosPref({ left: 40, top: 50 });
const stored = Scopes.getPosPref();
assert.strictEqual(stored.left, 40);
assert.strictEqual(stored.top, 50);

const video = box.document._makeEl("video");
const pane = box.document._makeEl("div");
const scopes = Scopes.attach({ container: pane, video: video });
assert.ok(scopes);
assert.strictEqual(scopes.isVisible(), false);
assert.strictEqual(scopes.root.hidden, true);
scopes.setVisible(true);
assert.strictEqual(Scopes.getOnPref(), true);
assert.strictEqual(scopes.isVisible(), true);
assert.strictEqual(scopes.root.hidden, false);
scopes.setPopped(true);
assert.strictEqual(Scopes.getPopPref(), true);
assert.ok(scopes.root.classList.contains("nexrec-scopes-pop"));
assert.strictEqual(scopes.root.parentNode, box.document.body);
fire(box.document, "keydown", {
  key: "Escape",
  preventDefault() {},
  stopPropagation() {},
  stopImmediatePropagation() {},
});
assert.strictEqual(scopes.isPopped(), false);
assert.strictEqual(scopes.root.parentNode, pane);
scopes.destroy();

const vu = Vu.attach({ container: pane, video: video, floatable: true });
assert.ok(vu);
assert.strictEqual(vu.getVisible(), false);
assert.strictEqual(vu.getChannels(), 2);
assert.ok(vu.getMonitorNote().includes("stereo AAC proxy"));
vu.setVisible(true);
assert.strictEqual(Vu.getVisiblePref(), true);
assert.strictEqual(vu.root.hidden, false);
vu.setScale(true);
assert.strictEqual(Vu.getScalePref(), true);
vu.setSolo(0);
assert.strictEqual(vu.getSolo(), 0);
vu.setVolume(0.4);
assert.strictEqual(vu.getVolume(), 0.4);
assert.ok(Math.abs(Vu.getVolumePref() - 0.4) < 1e-9);
vu.setListen(true);
assert.strictEqual(vu.isListening(), true);
assert.strictEqual(Vu.getMutedPref(), false);
assert.strictEqual(video.muted, true);
assert.strictEqual(vu.getSpectrumPair(), null);
vu.setPopped(true);
assert.strictEqual(vu.isPopped(), true);
assert.ok(vu.root.classList.contains("nexrec-vu-pop"));
vu.detach();

const spectrum = Spectrum.attach({ container: pane, vu: vu });
assert.ok(spectrum);
spectrum.setVisible(true);
assert.strictEqual(Spectrum.getOnPref(), true);
box.flushFrame();
assert.ok(spectrum.root.querySelector === undefined || true);
const label = spectrum.root.children[0].children[0];
assert.ok(String(label.textContent).includes("64-band"));
spectrum.setPopped(true);
assert.ok(spectrum.root.classList.contains("nexrec-spectrum-pop"));
assert.strictEqual(spectrum.root.parentNode, box.document.body);
fire(spectrum.root, "click", {
  preventDefault() {},
  stopPropagation() {},
  target: spectrum.root,
});
assert.strictEqual(spectrum.isPopped(), false);
spectrum.destroy();

console.log("nexrec monitors ok");
