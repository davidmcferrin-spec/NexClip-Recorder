#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const authSrc = fs.readFileSync(path.join(ROOT, "web", "public", "assets", "nexrec-auth-gate.js"), "utf8");

class FakeTrack {
  constructor(kind, id) {
    this.kind = kind;
    this.id = id;
    this._ended = [];
  }
  addEventListener(type, fn) {
    if (type === "ended") this._ended.push(fn);
  }
  stop() {}
}

class FakeStream {
  constructor() {
    this._tracks = [];
  }
  addTrack(t) { this._tracks.push(t); }
  getTracks() { return this._tracks.slice(); }
  getVideoTracks() { return this._tracks.filter((t) => t.kind === "video"); }
}

class FakePC {
  constructor() {
    FakePC.instances.push(this);
    this.connectionState = "new";
    this.iceConnectionState = "new";
    this.ontrack = null;
    this.onconnectionstatechange = null;
    this.oniceconnectionstatechange = null;
    this._closed = false;
    this._frames = 0;
  }
  addTransceiver() {}
  createOffer() {
    return Promise.resolve({ type: "offer", sdp: "v=0" });
  }
  setLocalDescription() { return Promise.resolve(); }
  setRemoteDescription() {
    this.connectionState = "connected";
    this.iceConnectionState = "connected";
    return Promise.resolve();
  }
  close() { this._closed = true; this.connectionState = "closed"; }
  getStats() {
    const frames = this._frames;
    const report = {
      forEach(fn) {
        fn({ type: "inbound-rtp", kind: "video", framesDecoded: frames });
      },
    };
    return Promise.resolve(report);
  }
}
FakePC.instances = [];

function loadAuth(fetchImpl) {
  const listeners = {};
  const video = {
    srcObject: null,
    _nexrecWhep: null,
    play() { return Promise.resolve(); },
    dispatchEvent() { return true; },
    addEventListener() {},
  };
  const sandbox = {
    window: null,
    document: {
      documentElement: { setAttribute() {}, getAttribute() { return "dark"; } },
      getElementById() { return null; },
      readyState: "complete",
      visibilityState: "visible",
      addEventListener(type, fn) {
        (listeners[type] = listeners[type] || []).push(fn);
      },
      removeEventListener(type, fn) {
        listeners[type] = (listeners[type] || []).filter((x) => x !== fn);
      },
      querySelectorAll() { return []; },
    },
    location: { pathname: "/live", search: "", href: "/live" },
    fetch: fetchImpl,
    RTCPeerConnection: FakePC,
    MediaStream: FakeStream,
    Event: function Event(type) { this.type = type; },
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    },
    URL: URL,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    console,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(authSrc, sandbox);
  return { auth: sandbox.NexRecAuth, video: video, listeners: listeners, sandbox: sandbox };
}

FakePC.instances = [];
let whepPosts = 0;
const first = loadAuth(function (url, opts) {
  const u = String(url);
  if (u.indexOf("action=whep_jwt") >= 0) {
    return Promise.resolve({
      ok: true,
      json() {
        return Promise.resolve({
          ok: true,
          whep_url: "http://mtx.test/in1/whep",
          jwt: "local",
          ice_servers: [],
        });
      },
    });
  }
  if (u.indexOf("/whep") >= 0 && opts && opts.method === "POST") {
    whepPosts += 1;
    return Promise.resolve({
      ok: true,
      status: 201,
      headers: { get(name) { return name.toLowerCase() === "location" ? "/in1/whep/sess1" : null; } },
      text() { return Promise.resolve("v=0"); },
    });
  }
  if (opts && opts.method === "DELETE") {
    return Promise.resolve({ ok: true, text() { return Promise.resolve(""); } });
  }
  return Promise.resolve({ ok: true, json() { return Promise.resolve({ ok: true }); } });
});

assert.strictEqual(typeof first.auth.whepConnect, "function");
assert.strictEqual(typeof first.auth.whepClose, "function");

first.auth.whepConnect(first.video, "in1").then(function () {
  assert.ok(first.video._nexrecWhep);
  assert.strictEqual(whepPosts, 1);
  assert.strictEqual(FakePC.instances.length, 1);
  const pc = FakePC.instances[0];
  const track = new FakeTrack("video", "v1");
  pc.ontrack({ track: track });
  assert.ok(first.video.srcObject);
  assert.strictEqual(first.video.srcObject.getVideoTracks().length, 1);

  // Stall: frames stop advancing past the watchdog threshold.
  pc._frames = 10;
  return new Promise(function (resolve) { setTimeout(resolve, 50); }).then(function () {
    // Force lastFrameAt into the past and trigger recover via failed state.
    first.video._nexrecWhep.lastFrames = 10;
    first.video._nexrecWhep.lastFrameAt = Date.now() - 20000;
    first.video._nexrecWhep.gotFrame = true;
    pc.connectionState = "failed";
    pc.onconnectionstatechange();
    return new Promise(function (resolve) { setTimeout(resolve, 2200); });
  }).then(function () {
    assert.ok(whepPosts >= 2, "expected WHEP reconnect after failure, posts=" + whepPosts);
    first.auth.whepClose(first.video);
    assert.strictEqual(first.video._nexrecWhep, null);
    assert.strictEqual(first.video.srcObject, null);
    console.log("whep reconnect ok");
  });
}).then(function () {
  // Background / inactive wake path: hide, age the hidden clock, return to visible.
  FakePC.instances = [];
  whepPosts = 0;
  const second = loadAuth(function (url, opts) {
    const u = String(url);
    if (u.indexOf("action=whep_jwt") >= 0) {
      return Promise.resolve({
        ok: true,
        json() {
          return Promise.resolve({
            ok: true,
            whep_url: "http://mtx.test/in2/whep",
            jwt: "local",
            ice_servers: [],
          });
        },
      });
    }
    if (u.indexOf("/whep") >= 0 && opts && opts.method === "POST") {
      whepPosts += 1;
      return Promise.resolve({
        ok: true,
        status: 201,
        headers: { get() { return null; } },
        text() { return Promise.resolve("v=0"); },
      });
    }
    if (opts && opts.method === "DELETE") {
      return Promise.resolve({ ok: true, text() { return Promise.resolve(""); } });
    }
    return Promise.resolve({ ok: true, json() { return Promise.resolve({ ok: true }); } });
  });
  return second.auth.whepConnect(second.video, "in2").then(function () {
    assert.strictEqual(whepPosts, 1);
    const st = second.video._nexrecWhep;
    st.gotFrame = true;
    st.lastFrameAt = Date.now();
    second.sandbox.document.visibilityState = "hidden";
    (second.listeners.visibilitychange || []).forEach(function (fn) { fn(); });
    assert.ok(st.hiddenAt > 0);
    st.hiddenAt = Date.now() - 5000;
    second.sandbox.document.visibilityState = "visible";
    (second.listeners.visibilitychange || []).forEach(function (fn) { fn(); });
    return new Promise(function (resolve) { setTimeout(resolve, 400); });
  }).then(function () {
    assert.ok(whepPosts >= 2, "expected reconnect after background wake, posts=" + whepPosts);
    second.auth.whepClose(second.video);
    console.log("whep background wake ok");
  });
}).catch(function (err) {
  console.error(err);
  process.exit(1);
});
