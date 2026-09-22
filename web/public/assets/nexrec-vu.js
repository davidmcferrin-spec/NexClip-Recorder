/**
 * nexrec-vu.js — Web Audio VU for the Live preview.
 *
 * Meters the decoded WHEP MediaStream (typically stereo AAC). Channel count
 * comes from the audio track / MediaStreamSource — never invented 8ch embeds.
 * 6ch uses 5.1 labels and an optional fold; anything else is L/R plus numbers.
 * 64-channel SDI/AES embed metering is NEXT (the proxy is stereo AAC, -ac 2).
 *
 * Spectrum (nexrec-spectrum.js) taps getSpectrumPair() on this graph.
 * Do not open a second MediaStreamSource.
 *
 * The <video> stays muted. Listen/mute and volume are this browser only
 * (Web Audio master gain). Not CALM / LKFS — that stays on the export editor.
 *
 * Per-browser prefs (VU default off, listen default muted):
 *   nexrec-vu-on          1 | 0
 *   nexrec-vu-scale       1 | 0
 *   nexrec-vu-pop         1 | 0
 *   nexrec-vu-pos         {"left":N,"top":N}
 *   nexrec-audio-muted    1 | 0
 *   nexrec-audio-volume   0..1
 *   nexrec-vu-solo        -1 | channel index
 *   nexrec-vu-playout     stereo | surround
 */
(function (global) {
  "use strict";

  const PREF_VISIBLE = "nexrec-vu-on";
  const PREF_SCALE = "nexrec-vu-scale";
  const PREF_POP = "nexrec-vu-pop";
  const PREF_POS = "nexrec-vu-pos";
  const PREF_MUTED = "nexrec-audio-muted";
  const PREF_VOLUME = "nexrec-audio-volume";
  const PREF_SOLO = "nexrec-vu-solo";
  const PREF_PLAYOUT = "nexrec-vu-playout";
  const DRAG_THRESHOLD = 6;
  const POS_PAD = 8;
  const MAX_CH = 8;
  const SCALE_MARKS_DB = [0, -6, -12, -20, -30, -40, -60];
  const ALIGN_DB = -20;
  const PROXY_LIMIT =
    "Meters follow the decoded preview channel count. " +
    "64-channel SDI/AES embed metering is NEXT; the WHEP proxy is stereo AAC.";

  const FFT = 2048;
  const SPEC_FFT = 8192;
  const SPEC_SMOOTH = 0.55;
  const SMOOTH = 0.3;
  const ATTACK = 0.35;
  const RELEASE = 0.06;
  const MIX_C = 0.707;
  const MIX_S = 0.707;
  const MIX_LFE = 0.5;

  function dbScaleLabel(db) {
    return db === 0 ? "FS" : String(db);
  }

  function dbFromPeak(peak) {
    if (peak <= 0.00001) return -60;
    return 20 * Math.log10(peak);
  }

  function heightFromDb(db) {
    const n = (db + 60) / 60;
    return Math.max(0, Math.min(100, n * 100));
  }

  /**
   * Honest meter labels for a decoded channel count.
   * count 0 → assumed stereo (proxy default) with assumed:true.
   * Above MAX_CH is truncated and limited:true (64-ch embed is NEXT).
   */
  function describeChannels(count) {
    const raw = count | 0;
    const assumed = raw <= 0;
    const limited = raw > MAX_CH;
    const n = assumed ? 2 : Math.min(MAX_CH, raw);
    let labels;
    if (n === 1) labels = ["M"];
    else if (n === 2) labels = ["L", "R"];
    else if (n === 6) labels = ["L", "R", "C", "LFE", "Ls", "Rs"];
    else if (n === 8) labels = ["L", "R", "C", "LFE", "Ls", "Rs", "7", "8"];
    else {
      labels = [];
      for (let i = 0; i < n; i++) {
        labels.push(i === 0 ? "L" : i === 1 ? "R" : String(i + 1));
      }
    }
    return {
      count: n,
      labels: labels,
      assumed: assumed,
      limited: limited,
      proxyStereo: n <= 2,
      has51: n >= 6,
    };
  }

  function resolveChannelCount(detected, nodeCount) {
    const d = detected | 0;
    const node = nodeCount | 0;
    return describeChannels(d || node || 0);
  }

  function monitorNote(info, hasAudio) {
    const base = !info || info.proxyStereo
      ? "stereo AAC proxy"
      : (info.count + " ch" + (info.limited ? " (first " + MAX_CH + ")" : ""));
    return hasAudio ? base : base + " · waiting";
  }

  function prefFlag(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null || raw === "") return fallback;
      return raw === "1" || raw === "true";
    } catch (e) {
      return fallback;
    }
  }

  function setFlag(key, on) {
    try { localStorage.setItem(key, on ? "1" : "0"); } catch (e) { /* private mode */ }
    return !!on;
  }

  function getVisiblePref() { return prefFlag(PREF_VISIBLE, false); }
  function setVisiblePref(on) { return setFlag(PREF_VISIBLE, on); }
  function getScalePref() { return prefFlag(PREF_SCALE, false); }
  function setScalePref(on) { return setFlag(PREF_SCALE, on); }
  function getPopPref() { return prefFlag(PREF_POP, false); }
  function setPopPref(on) { return setFlag(PREF_POP, on); }

  function getMutedPref() { return prefFlag(PREF_MUTED, true); }
  function setMutedPref(on) { return setFlag(PREF_MUTED, on); }

  function getVolumePref() {
    try {
      const raw = localStorage.getItem(PREF_VOLUME);
      if (raw === null || raw === "") return 0.2;
      const n = Number(raw);
      if (!Number.isFinite(n)) return 0.2;
      return Math.max(0, Math.min(1, n));
    } catch (e) {
      return 0.2;
    }
  }

  function setVolumePref(v) {
    const n = Math.max(0, Math.min(1, Number(v)));
    const out = Number.isFinite(n) ? n : 0.2;
    try { localStorage.setItem(PREF_VOLUME, String(out)); } catch (e) { /* private mode */ }
    return out;
  }

  function getPlayoutPref() {
    try {
      return localStorage.getItem(PREF_PLAYOUT) === "surround" ? "surround" : "stereo";
    } catch (e) {
      return "stereo";
    }
  }

  function setPlayoutPref(mode) {
    const v = mode === "surround" ? "surround" : "stereo";
    try { localStorage.setItem(PREF_PLAYOUT, v); } catch (e) { /* private mode */ }
    return v;
  }

  function getSoloPref() {
    try {
      const raw = localStorage.getItem(PREF_SOLO);
      if (raw === null || raw === "" || raw === "-1") return -1;
      const n = parseInt(raw, 10);
      if (!Number.isFinite(n) || n < 0) return -1;
      return Math.min(MAX_CH - 1, n);
    } catch (e) {
      return -1;
    }
  }

  function setSoloPref(ch) {
    const v = ch === null || ch === undefined || ch < 0 ? -1 : Math.min(MAX_CH - 1, ch | 0);
    try { localStorage.setItem(PREF_SOLO, String(v)); } catch (e) { /* private mode */ }
    return v;
  }

  function parsePos(raw) {
    if (raw == null || raw === "") return null;
    let o = raw;
    if (typeof raw === "string") {
      try { o = JSON.parse(raw); } catch (e) { return null; }
    }
    if (!o || typeof o !== "object") return null;
    const left = Number(o.left);
    const top = Number(o.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
    return { left: left, top: top };
  }

  function clampPos(left, top, width, height, vw, vh, pad) {
    pad = pad == null ? POS_PAD : pad;
    width = Math.max(0, Number(width) || 0);
    height = Math.max(0, Number(height) || 0);
    vw = Math.max(0, Number(vw) || 0);
    vh = Math.max(0, Number(vh) || 0);
    const maxL = Math.max(pad, vw - width - pad);
    const maxT = Math.max(pad, vh - height - pad);
    return {
      left: Math.min(maxL, Math.max(pad, left)),
      top: Math.min(maxT, Math.max(pad, top)),
    };
  }

  function getPosPref() {
    try { return parsePos(localStorage.getItem(PREF_POS)); }
    catch (e) { return null; }
  }

  function setPosPref(pos) {
    const next = parsePos(pos);
    try {
      if (!next) localStorage.removeItem(PREF_POS);
      else localStorage.setItem(PREF_POS, JSON.stringify(next));
    } catch (e) { /* private mode */ }
    return next;
  }

  let sharedCtx = null;

  function ensureCtx() {
    if (sharedCtx) return sharedCtx;
    const AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    sharedCtx = new AC();
    return sharedCtx;
  }

  function resume() {
    const ctx = ensureCtx();
    if (ctx && ctx.state === "suspended") {
      try { return ctx.resume(); } catch (e) { /* autoplay policy */ }
    }
    return Promise.resolve(ctx);
  }

  function ensureStyles() {
    if (document.getElementById("nexrec-vu-css")) return;
    const s = document.createElement("style");
    s.id = "nexrec-vu-css";
    s.textContent = `
.nexrec-vu {
  position: absolute; top: 36px; bottom: 8px; right: 8px; z-index: 4;
  display: flex; flex-direction: column; align-items: stretch; gap: 6px;
  width: min-content; max-width: 48%; pointer-events: auto;
  padding: 6px 8px 8px;
  background: rgba(8, 12, 16, .78);
  border: 1px solid rgba(44, 53, 66, .9);
  border-radius: 3px;
  cursor: pointer;
  font: 10px/1.2 ui-monospace, "Cascadia Mono", Consolas, monospace;
  color: var(--text, #d6dde6);
}
.nexrec-vu[hidden] { display: none !important; }
.nexrec-vu.nexrec-vu-pop {
  position: fixed; left: 12px; top: 12px; right: auto; bottom: auto;
  z-index: 55; height: min(72vh, 560px); max-width: none;
  background: var(--panel, #1d232b);
  border: 1px solid var(--edge, #2c3542);
  border-radius: 4px;
  box-shadow: var(--shadow, none);
  cursor: grab; touch-action: none;
}
.nexrec-vu.nexrec-vu-pop.nexrec-vu-drag { cursor: grabbing; }
.nexrec-vu-toolbar {
  display: flex; gap: 3px; justify-content: flex-end; flex-wrap: wrap; align-items: center;
}
.nexrec-vu-toolbar button {
  background: var(--badge-bg, rgba(20,24,29,.85)); color: var(--dim, #98a6b5);
  border: 1px solid var(--edge, #2c3542); border-radius: 3px;
  padding: 2px 6px; font: inherit; cursor: pointer; line-height: 1.2;
}
.nexrec-vu-toolbar button:hover { color: var(--text, #d6dde6); border-color: var(--acc, #3ecf8e); }
.nexrec-vu-toolbar button.active {
  color: var(--on-acc, #08131a); background: var(--acc, #3ecf8e);
  border-color: var(--acc, #3ecf8e); font-weight: 600;
}
.nexrec-vu-toolbar button:disabled { opacity: .35; cursor: not-allowed; }
.nexrec-vu-toolbar button[hidden] { display: none !important; }
.nexrec-vu-vol {
  width: 64px; height: 14px; margin: 0; padding: 0;
  accent-color: var(--acc, #3ecf8e); cursor: pointer;
}
.nexrec-vu-note {
  color: #98a6b5; font-size: 9px; letter-spacing: .02em; text-align: right;
}
.nexrec-vu-meter-row {
  flex: 1; min-height: 72px; display: flex; flex-direction: row;
  align-items: stretch; gap: 3px; justify-content: flex-end;
}
.nexrec-vu-scale {
  position: relative; width: 28px; flex: 0 0 28px;
  margin-bottom: 12px; pointer-events: none;
  color: #e8eef4; font-size: 9px; line-height: 1;
}
.nexrec-vu-scale[hidden] { display: none !important; }
.nexrec-vu-scale-mark {
  position: absolute; left: 0; right: 2px; text-align: right;
  transform: translateY(50%); white-space: nowrap;
  text-shadow: 0 0 3px #000, 0 1px 2px #000;
}
.nexrec-vu-scale-mark.fs { color: #e5484d; font-weight: 500; }
.nexrec-vu-scale-mark.align { color: #56c4f5; font-weight: 600; }
.nexrec-vu-bars {
  flex: 0 0 auto; min-height: 0; display: flex; flex-direction: row;
  align-items: stretch; gap: 3px;
}
.nexrec-vu-track::after {
  content: ""; position: absolute; inset: 0; z-index: 1; pointer-events: none;
  background:
    linear-gradient(to top, transparent calc(90% - .5px), rgba(214,221,230,.35) calc(90% - .5px), rgba(214,221,230,.35) calc(90% + .5px), transparent calc(90% + .5px)),
    linear-gradient(to top, transparent calc(80% - .5px), rgba(214,221,230,.35) calc(80% - .5px), rgba(214,221,230,.35) calc(80% + .5px), transparent calc(80% + .5px)),
    linear-gradient(to top, transparent calc(66.667% - 1px), #56c4f5 calc(66.667% - 1px), #56c4f5 calc(66.667% + 1px), transparent calc(66.667% + 1px)),
    linear-gradient(to top, transparent calc(50% - .5px), rgba(214,221,230,.18) calc(50% - .5px), rgba(214,221,230,.18) calc(50% + .5px), transparent calc(50% + .5px)),
    linear-gradient(to top, transparent calc(33.333% - .5px), rgba(214,221,230,.14) calc(33.333% - .5px), rgba(214,221,230,.14) calc(33.333% + .5px), transparent calc(33.333% + .5px));
}
.nexrec-vu-ch {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  min-width: 16px; flex: 0 0 auto; cursor: pointer;
  background: transparent; border: none; padding: 0; color: inherit; font: inherit;
}
.nexrec-vu-ch:focus-visible { outline: 2px solid var(--acc, #3ecf8e); outline-offset: 1px; }
.nexrec-vu-track {
  flex: 1; width: 10px; min-height: 48px; position: relative;
  background: rgba(0,0,0,.55); border: 1px solid var(--edge, #2c3542);
  border-radius: 2px; overflow: hidden;
}
.nexrec-vu-pop .nexrec-vu-track { width: 16px; background: #05080b; }
.nexrec-vu-ch.solo .nexrec-vu-track {
  border-color: var(--acc, #3ecf8e); box-shadow: 0 0 0 1px var(--acc, #3ecf8e);
}
.nexrec-vu-ch.dimmed { opacity: .45; }
.nexrec-vu-fill {
  position: absolute; left: 0; right: 0; bottom: 0; height: 0%; z-index: 0;
  background: linear-gradient(to top,
    var(--ok, #4cc38a) 0%,
    var(--ok, #4cc38a) 55%,
    var(--warn, #f5a623) 75%,
    var(--bad, #e5484d) 92%);
  transition: height 50ms linear;
}
.nexrec-vu-peak {
  position: absolute; left: 0; right: 0; height: 2px; z-index: 2;
  background: #fff; opacity: .9; pointer-events: none;
}
.nexrec-vu-label { font-size: 9px; color: var(--dim, #98a6b5); }
.nexrec-vu-ch.solo .nexrec-vu-label { color: var(--acc, #3ecf8e); font-weight: 600; }
`;
    document.head.appendChild(s);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  /**
   * @param {object} opts
   * @param {HTMLElement} opts.container
   * @param {HTMLVideoElement} opts.video
   * @param {MediaStream|null} [opts.stream]
   * @param {boolean} [opts.floatable=true]
   */
  function attach(opts) {
    ensureStyles();
    const container = opts && opts.container;
    const video = opts && opts.video;
    if (!container || !video) return null;
    const floatable = opts.floatable !== undefined ? !!opts.floatable : true;

    const root = el("div", "nexrec-vu");
    root.hidden = true;
    const toolbar = el("div", "nexrec-vu-toolbar");
    const btnMute = el("button", "", "Listen");
    btnMute.type = "button";
    btnMute.setAttribute("data-vu", "mute");
    const vol = document.createElement("input");
    vol.type = "range";
    vol.className = "nexrec-vu-vol";
    vol.min = "0";
    vol.max = "1";
    vol.step = "0.05";
    vol.setAttribute("aria-label", "Preview listen volume");
    const btnStereo = el("button", "", "St");
    btnStereo.type = "button";
    btnStereo.hidden = true;
    btnStereo.title = "Fold to stereo";
    const btnSurround = el("button", "", "5.1");
    btnSurround.type = "button";
    btnSurround.hidden = true;
    btnSurround.title = "Discrete 5.1 to this computer";
    const btnScale = el("button", "", "dB");
    btnScale.type = "button";
    btnScale.title = "Show dBFS scale (this browser only)";
    const allBtn = el("button", "", "ALL");
    allBtn.type = "button";
    allBtn.title = "Clear engineering solo";
    toolbar.appendChild(btnMute);
    toolbar.appendChild(vol);
    toolbar.appendChild(btnStereo);
    toolbar.appendChild(btnSurround);
    toolbar.appendChild(btnScale);
    toolbar.appendChild(allBtn);

    const note = el("div", "nexrec-vu-note");
    note.title = PROXY_LIMIT;

    const meterRow = el("div", "nexrec-vu-meter-row");
    const scaleEl = el("div", "nexrec-vu-scale");
    scaleEl.hidden = true;
    scaleEl.setAttribute("aria-hidden", "true");
    const barsEl = el("div", "nexrec-vu-bars");
    barsEl.setAttribute("role", "group");
    barsEl.setAttribute("aria-label", "Audio level meters");
    meterRow.appendChild(scaleEl);
    meterRow.appendChild(barsEl);
    root.appendChild(toolbar);
    root.appendChild(note);
    root.appendChild(meterRow);
    container.appendChild(root);

    let channelInfo = describeChannels(0);
    let listen = !getMutedPref();
    let visible = opts.visible !== undefined ? !!opts.visible : getVisiblePref();
    let scaleOn = opts.scale !== undefined ? !!opts.scale : getScalePref();
    let volume = opts.volume !== undefined ? Number(opts.volume) : getVolumePref();
    if (!Number.isFinite(volume)) volume = 0.2;
    volume = Math.max(0, Math.min(1, volume));
    let playout = getPlayoutPref();
    let solo = getSoloPref();
    let hasAudio = false;
    vol.value = String(volume);

    let ctx = null;
    let source = null;
    let splitter = null;
    let masterGain = null;
    let analysers = [];
    let specL = null;
    let specR = null;
    let chGains = [];
    let outNodes = [];
    let fills = [];
    let peaks = [];
    let chBtns = [];
    let raf = 0;
    let levels = [];
    let peakHold = [];
    let connectedStreamId = null;
    let timeData = null;
    let popped = floatable ? getPopPref() : false;
    let pos = floatable ? getPosPref() : null;
    let drag = null;
    let suppressClick = false;

    function playoutMode() {
      if (!channelInfo.has51) return "stereo";
      return playout === "surround" ? "surround" : "fold";
    }

    function channelInProgram(i) {
      const mode = playoutMode();
      if (mode === "surround" || mode === "fold") return i < Math.min(6, channelInfo.count);
      return i < Math.min(2, channelInfo.count);
    }

    function applyPosition() {
      if (!(floatable && popped && visible) || !pos) {
        if (!(floatable && popped && visible)) {
          root.style.left = "";
          root.style.top = "";
          root.style.bottom = "";
        }
        return;
      }
      const w = root.offsetWidth || 0;
      const h = root.offsetHeight || 0;
      const next = clampPos(pos.left, pos.top, w, h, window.innerWidth || 0, window.innerHeight || 0, POS_PAD);
      if (w > 0 && h > 0 && (next.left !== pos.left || next.top !== pos.top)) {
        pos = next;
        setPosPref(pos);
      }
      root.style.left = next.left + "px";
      root.style.top = next.top + "px";
      root.style.bottom = "auto";
    }

    function applyHost() {
      if (!floatable) return;
      const parent = (popped && visible) ? (document.body || container) : container;
      if (parent && root.parentNode !== parent) parent.appendChild(root);
      root.classList.toggle("nexrec-vu-pop", !!(visible && popped));
      root.classList.toggle("nexrec-vu-drag", !!(drag && drag.moved));
      root.title = (popped
        ? "Drag to move · click empty area to dock · Esc. "
        : "Click empty area to float (buttons still work). ") + PROXY_LIMIT;
      applyPosition();
    }

    function setPopped(on) {
      if (!floatable) return false;
      on = !!on;
      if (popped === on) {
        applyHost();
        return popped;
      }
      popped = setPopPref(on);
      applyHost();
      return popped;
    }

    function paintNote() {
      note.textContent = monitorNote(channelInfo, hasAudio);
    }

    function updateRootVisibility() {
      root.hidden = !visible;
      paintNote();
      applyHost();
      if (visible && hasAudio && analysers.length && !raf) {
        raf = requestAnimationFrame(tick);
      } else if ((!visible || !hasAudio) && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    }

    function setVisible(on) {
      visible = setVisiblePref(!!on);
      updateRootVisibility();
      return visible;
    }

    function paintScale() {
      btnScale.classList.toggle("active", scaleOn);
      btnScale.setAttribute("aria-pressed", scaleOn ? "true" : "false");
      if (!scaleOn) {
        scaleEl.hidden = true;
        scaleEl.setAttribute("aria-hidden", "true");
        scaleEl.textContent = "";
        while (scaleEl.firstChild) scaleEl.removeChild(scaleEl.firstChild);
        return;
      }
      scaleEl.hidden = false;
      scaleEl.setAttribute("aria-hidden", "false");
      while (scaleEl.firstChild) scaleEl.removeChild(scaleEl.firstChild);
      SCALE_MARKS_DB.forEach(function (db) {
        const mark = el("span", "nexrec-vu-scale-mark" +
          (db === 0 ? " fs" : (db === ALIGN_DB ? " align" : "")));
        mark.textContent = dbScaleLabel(db);
        mark.style.bottom = heightFromDb(db).toFixed(1) + "%";
        scaleEl.appendChild(mark);
      });
    }

    function setScale(on) {
      scaleOn = setScalePref(!!on);
      paintScale();
      return scaleOn;
    }

    function paintMute() {
      btnMute.textContent = listen ? "Mute" : "Listen";
      btnMute.classList.toggle("active", listen);
      btnMute.title = listen
        ? "Mute preview audio (this browser only)"
        : "Listen to preview audio (this browser only)";
      btnMute.setAttribute("aria-pressed", listen ? "true" : "false");
    }

    function paintToolbar() {
      const mode = playoutMode();
      btnStereo.hidden = !channelInfo.has51;
      btnSurround.hidden = !channelInfo.has51;
      btnStereo.classList.toggle("active", channelInfo.has51 && mode === "fold");
      btnSurround.classList.toggle("active", channelInfo.has51 && mode === "surround");
      allBtn.classList.toggle("active", solo < 0);
      chBtns.forEach(function (btn, i) {
        const isSolo = solo === i;
        btn.classList.toggle("solo", isSolo);
        btn.classList.toggle("dimmed", solo >= 0 && !isSolo);
        btn.setAttribute("aria-pressed", isSolo ? "true" : "false");
      });
      paintMute();
      paintNote();
    }

    function effectiveMasterGain() {
      return listen ? volume : 0;
    }

    function applyRouting() {
      chGains.forEach(function (g, i) {
        if (!g) return;
        const audible = solo < 0 ? channelInProgram(i) : solo === i;
        g.gain.value = audible ? 1 : 0;
      });
      if (masterGain) masterGain.gain.value = effectiveMasterGain();
      paintToolbar();
    }

    function setVolume(v) {
      volume = Math.max(0, Math.min(1, Number(v)));
      if (!Number.isFinite(volume)) volume = 0.2;
      setVolumePref(volume);
      vol.value = String(volume);
      if (masterGain) masterGain.gain.value = effectiveMasterGain();
      return volume;
    }

    function setSolo(ch) {
      if (ch === solo && ch >= 0) solo = setSoloPref(-1);
      else if (ch === null || ch < 0) solo = setSoloPref(-1);
      else solo = setSoloPref(Math.min(channelInfo.count - 1, ch | 0));
      applyRouting();
    }

    function setPlayout(mode) {
      playout = setPlayoutPref(mode);
      if (connectedStreamId && video.srcObject) buildGraph(video.srcObject);
      else paintToolbar();
    }

    function setListen(on) {
      listen = !!on;
      setMutedPref(!listen);
      if (masterGain) masterGain.gain.value = effectiveMasterGain();
      video.muted = true;
      if (listen) resume();
      paintMute();
    }

    function rebuildMeterDom() {
      while (barsEl.firstChild) barsEl.removeChild(barsEl.firstChild);
      fills = [];
      peaks = [];
      chBtns = [];
      const labels = channelInfo.labels;
      for (let i = 0; i < channelInfo.count; i++) {
        const lab = labels[i] || String(i + 1);
        const btn = el("button", "nexrec-vu-ch");
        btn.type = "button";
        btn.title = "Solo " + lab + " (this browser only)";
        btn.setAttribute("aria-label", "Solo audio " + lab);
        const track = el("span", "nexrec-vu-track");
        const fill = el("span", "nexrec-vu-fill");
        const peak = el("span", "nexrec-vu-peak");
        peak.style.bottom = "0%";
        track.appendChild(fill);
        track.appendChild(peak);
        btn.appendChild(track);
        btn.appendChild(el("span", "nexrec-vu-label", lab));
        btn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          ev.preventDefault();
          resume();
          setSolo(i);
        });
        barsEl.appendChild(btn);
        chBtns.push(btn);
        fills.push(fill);
        peaks.push(peak);
      }
      levels = new Array(channelInfo.count).fill(0);
      peakHold = new Array(channelInfo.count).fill(0);
      if (solo >= channelInfo.count) solo = setSoloPref(-1);
      paintToolbar();
    }

    function listenPair() {
      if (channelInfo.count <= 1) return [0, 0];
      return [0, 1];
    }

    function wireSpectrum() {
      specL = null;
      specR = null;
      if (!ctx || !splitter) return;
      specL = ctx.createAnalyser();
      specR = ctx.createAnalyser();
      [specL, specR].forEach(function (a) {
        a.fftSize = SPEC_FFT;
        a.smoothingTimeConstant = SPEC_SMOOTH;
        a.minDecibels = -60;
        a.maxDecibels = 0;
      });
      const pair = listenPair();
      splitter.connect(specL, pair[0]);
      splitter.connect(specR, pair[1]);
    }

    function teardownGraph() {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      [source, splitter, masterGain, specL, specR].concat(analysers, chGains, outNodes).forEach(function (n) {
        try { if (n) n.disconnect(); } catch (e) { /* ignore */ }
      });
      source = null;
      splitter = null;
      masterGain = null;
      specL = null;
      specR = null;
      analysers = [];
      chGains = [];
      outNodes = [];
      connectedStreamId = null;
    }

    function tick() {
      raf = 0;
      if (!visible || !analysers.length) return;
      for (let i = 0; i < analysers.length; i++) {
        const a = analysers[i];
        if (!timeData || timeData.length !== a.fftSize) {
          timeData = new Float32Array(a.fftSize);
        }
        a.getFloatTimeDomainData(timeData);
        let peak = 0;
        for (let j = 0; j < timeData.length; j++) {
          const v = Math.abs(timeData[j]);
          if (v > peak) peak = v;
        }
        const prev = levels[i] || 0;
        const next = peak > prev
          ? prev + (peak - prev) * ATTACK
          : prev + (peak - prev) * RELEASE;
        levels[i] = next;
        peakHold[i] = Math.max(peakHold[i] * 0.985, next);
        if (fills[i]) fills[i].style.height = heightFromDb(dbFromPeak(next)).toFixed(1) + "%";
        if (peaks[i]) peaks[i].style.bottom = heightFromDb(dbFromPeak(peakHold[i])).toFixed(1) + "%";
      }
      raf = requestAnimationFrame(tick);
    }

    function connectFold(gain, i, merger) {
      const coeffsL = [1, 0, MIX_C, MIX_LFE, MIX_S, 0];
      const coeffsR = [0, 1, MIX_C, MIX_LFE, 0, MIX_S];
      if (i < 6 && coeffsL[i]) {
        const gL = ctx.createGain();
        gL.gain.value = coeffsL[i];
        gain.connect(gL);
        gL.connect(merger, 0, 0);
        outNodes.push(gL);
      }
      if (i < 6 && coeffsR[i]) {
        const gR = ctx.createGain();
        gR.gain.value = coeffsR[i];
        gain.connect(gR);
        gR.connect(merger, 0, 1);
        outNodes.push(gR);
      }
    }

    function configureDestination(outChannels) {
      try {
        const dest = ctx.destination;
        const max = dest.maxChannelCount || 2;
        const n = Math.min(outChannels, max);
        dest.channelCount = Math.max(2, n);
        dest.channelCountMode = "explicit";
        dest.channelInterpretation = outChannels > 2 ? "discrete" : "speakers";
      } catch (e) { /* some browsers reject */ }
    }

    function buildGraph(stream) {
      teardownGraph();
      hasAudio = false;
      ctx = ensureCtx();
      if (!ctx || !stream || typeof stream.getAudioTracks !== "function") {
        channelInfo = describeChannels(0);
        rebuildMeterDom();
        updateRootVisibility();
        return;
      }
      const audioTracks = stream.getAudioTracks();
      if (!audioTracks.length) {
        channelInfo = describeChannels(0);
        rebuildMeterDom();
        updateRootVisibility();
        return;
      }

      let detected = 0;
      try {
        const st = audioTracks[0].getSettings && audioTracks[0].getSettings();
        if (st && st.channelCount) detected = st.channelCount | 0;
      } catch (e) { /* ignore */ }

      try {
        const audioStream = new MediaStream(audioTracks);
        source = ctx.createMediaStreamSource(audioStream);
        let nodeCount = 0;
        try { nodeCount = source.channelCount | 0; } catch (e) { /* ignore */ }
        channelInfo = resolveChannelCount(detected, nodeCount);
        rebuildMeterDom();
        try {
          source.channelCountMode = "explicit";
          source.channelInterpretation = "discrete";
          source.channelCount = channelInfo.count;
        } catch (e) { /* MediaStreamSource may ignore */ }

        splitter = ctx.createChannelSplitter(channelInfo.count);
        try {
          splitter.channelCount = channelInfo.count;
          splitter.channelCountMode = "explicit";
          splitter.channelInterpretation = "discrete";
        } catch (e) { /* ignore */ }
        masterGain = ctx.createGain();
        masterGain.gain.value = effectiveMasterGain();
        source.connect(splitter);
        wireSpectrum();

        const mode = playoutMode();
        const outN = mode === "surround" ? Math.min(6, channelInfo.count) : 2;
        const merger = ctx.createChannelMerger(outN);
        outNodes.push(merger);
        analysers = [];
        chGains = [];
        for (let i = 0; i < channelInfo.count; i++) {
          const analyser = ctx.createAnalyser();
          analyser.fftSize = FFT;
          analyser.smoothingTimeConstant = SMOOTH;
          const gain = ctx.createGain();
          gain.gain.value = 1;
          splitter.connect(analyser, i);
          splitter.connect(gain, i);
          analysers.push(analyser);
          chGains.push(gain);
          if (mode === "surround") {
            if (i < outN) gain.connect(merger, 0, i);
          } else if (mode === "fold") {
            connectFold(gain, i, merger);
          } else if (channelInfo.count === 1) {
            gain.connect(merger, 0, 0);
            gain.connect(merger, 0, 1);
          } else if (i === 0) {
            gain.connect(merger, 0, 0);
          } else if (i === 1) {
            gain.connect(merger, 0, 1);
          } else {
            gain.connect(merger, 0, 0);
            gain.connect(merger, 0, 1);
          }
        }
        merger.connect(masterGain);
        masterGain.connect(ctx.destination);
        configureDestination(outN);
        connectedStreamId = stream.id || "stream";
        hasAudio = true;
        applyRouting();
        video.muted = true;
        updateRootVisibility();
      } catch (err) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("nexrec-vu: graph failed", err);
        }
        teardownGraph();
        hasAudio = false;
        updateRootVisibility();
      }
    }

    function stop(ev) {
      ev.stopPropagation();
    }

    btnMute.addEventListener("click", function (ev) {
      stop(ev);
      ev.preventDefault();
      resume();
      setListen(!listen);
    });
    vol.addEventListener("input", function (ev) {
      stop(ev);
      setVolume(vol.value);
    });
    vol.addEventListener("click", stop);
    vol.addEventListener("pointerdown", stop);
    btnStereo.addEventListener("click", function (ev) {
      stop(ev);
      resume();
      setPlayout("stereo");
    });
    btnSurround.addEventListener("click", function (ev) {
      stop(ev);
      resume();
      if (channelInfo.has51) setPlayout("surround");
    });
    btnScale.addEventListener("click", function (ev) {
      stop(ev);
      setScale(!scaleOn);
    });
    allBtn.addEventListener("click", function (ev) {
      stop(ev);
      resume();
      setSolo(-1);
    });

    function keepElementMuted() {
      if (!video.muted) video.muted = true;
    }
    video.addEventListener("volumechange", keepElementMuted);
    video.muted = true;

    function onPointerDown(ev) {
      if (!floatable || !popped || !visible) return;
      if (ev.target && ev.target.closest && ev.target.closest("button, input")) return;
      if (ev.button != null && ev.button !== 0) return;
      ev.preventDefault();
      const rect = root.getBoundingClientRect();
      drag = {
        pointerId: ev.pointerId,
        startX: ev.clientX,
        startY: ev.clientY,
        origL: rect.left,
        origT: rect.top,
        moved: false,
      };
      try { root.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
    }

    function onPointerMove(ev) {
      if (!drag || ev.pointerId !== drag.pointerId) return;
      const dx = ev.clientX - drag.startX;
      const dy = ev.clientY - drag.startY;
      if (!drag.moved && (dx * dx + dy * dy) < DRAG_THRESHOLD * DRAG_THRESHOLD) return;
      ev.preventDefault();
      drag.moved = true;
      pos = clampPos(
        drag.origL + dx,
        drag.origT + dy,
        root.offsetWidth || 0,
        root.offsetHeight || 0,
        window.innerWidth || 0,
        window.innerHeight || 0,
        POS_PAD
      );
      applyHost();
    }

    function endDrag(ev) {
      if (!drag || (ev && ev.pointerId != null && ev.pointerId !== drag.pointerId)) return;
      const moved = drag.moved;
      const pointerId = drag.pointerId;
      drag = null;
      root.classList.remove("nexrec-vu-drag");
      try { root.releasePointerCapture(pointerId); } catch (e) { /* ignore */ }
      if (moved) {
        pos = setPosPref(pos) || pos;
        suppressClick = true;
      }
    }

    function onRootClick(ev) {
      if (!floatable) return;
      if (ev.target && ev.target.closest && ev.target.closest("button, input")) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (suppressClick) {
        suppressClick = false;
        return;
      }
      setPopped(!popped);
    }

    function onResize() {
      if (!floatable || !popped || !visible || !pos) return;
      applyPosition();
    }

    function onKey(ev) {
      if (!floatable) return;
      if (ev.key !== "Escape") return;
      if (!popped || !visible) return;
      if (document.querySelector("dialog[open]")) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (typeof ev.stopImmediatePropagation === "function") ev.stopImmediatePropagation();
      setPopped(false);
    }

    if (floatable) {
      root.addEventListener("pointerdown", onPointerDown);
      root.addEventListener("pointermove", onPointerMove);
      root.addEventListener("pointerup", endDrag);
      root.addEventListener("pointercancel", endDrag);
      root.addEventListener("click", onRootClick);
      document.addEventListener("keydown", onKey, true);
      window.addEventListener("resize", onResize);
    }

    rebuildMeterDom();
    paintScale();
    applyHost();
    updateRootVisibility();
    if (opts.stream) buildGraph(opts.stream);

    return {
      root: root,
      setStream: function (stream) {
        if (!stream) {
          teardownGraph();
          hasAudio = false;
          channelInfo = describeChannels(0);
          rebuildMeterDom();
          updateRootVisibility();
          return;
        }
        const id = stream.id || "";
        if (id && id === connectedStreamId && analysers.length) {
          hasAudio = true;
          updateRootVisibility();
          return;
        }
        buildGraph(stream);
      },
      setListen: setListen,
      isListening: function () { return listen; },
      setVolume: setVolume,
      getVolume: function () { return volume; },
      setVisible: setVisible,
      getVisible: function () { return visible; },
      setPopped: setPopped,
      isPopped: function () { return popped; },
      setScale: setScale,
      getScale: function () { return scaleOn; },
      setPlayout: setPlayout,
      setSolo: setSolo,
      getSolo: function () { return solo; },
      getChannels: function () { return channelInfo.count; },
      getChannelInfo: function () { return channelInfo; },
      getMonitorNote: function () { return monitorNote(channelInfo, hasAudio); },
      hasAudio: function () { return hasAudio; },
      getSpectrumPair: function () {
        if (!ctx || !specL || !specR) return null;
        return {
          left: specL,
          right: specR,
          sampleRate: ctx.sampleRate,
          fftSize: specL.fftSize,
        };
      },
      resume: resume,
      detach: function () {
        teardownGraph();
        video.removeEventListener("volumechange", keepElementMuted);
        if (floatable) {
          root.removeEventListener("pointerdown", onPointerDown);
          root.removeEventListener("pointermove", onPointerMove);
          root.removeEventListener("pointerup", endDrag);
          root.removeEventListener("pointercancel", endDrag);
          root.removeEventListener("click", onRootClick);
          document.removeEventListener("keydown", onKey, true);
          window.removeEventListener("resize", onResize);
        }
        if (root.parentNode) root.parentNode.removeChild(root);
      },
    };
  }

  global.NexRecVu = {
    MAX_CH: MAX_CH,
    SPEC_FFT: SPEC_FFT,
    SCALE_MARKS_DB: SCALE_MARKS_DB,
    ALIGN_DB: ALIGN_DB,
    PROXY_LIMIT: PROXY_LIMIT,
    DRAG_THRESHOLD: DRAG_THRESHOLD,
    POS_PAD: POS_PAD,
    PREF_VISIBLE: PREF_VISIBLE,
    PREF_SCALE: PREF_SCALE,
    PREF_POP: PREF_POP,
    PREF_POS: PREF_POS,
    PREF_MUTED: PREF_MUTED,
    PREF_VOLUME: PREF_VOLUME,
    PREF_SOLO: PREF_SOLO,
    PREF_PLAYOUT: PREF_PLAYOUT,
    dbScaleLabel: dbScaleLabel,
    dbFromPeak: dbFromPeak,
    heightFromDb: heightFromDb,
    describeChannels: describeChannels,
    resolveChannelCount: resolveChannelCount,
    monitorNote: monitorNote,
    parsePos: parsePos,
    clampPos: clampPos,
    getVisiblePref: getVisiblePref,
    setVisiblePref: setVisiblePref,
    getScalePref: getScalePref,
    setScalePref: setScalePref,
    getPopPref: getPopPref,
    setPopPref: setPopPref,
    getMutedPref: getMutedPref,
    setMutedPref: setMutedPref,
    getVolumePref: getVolumePref,
    setVolumePref: setVolumePref,
    getSoloPref: getSoloPref,
    setSoloPref: setSoloPref,
    getPlayoutPref: getPlayoutPref,
    setPlayoutPref: setPlayoutPref,
    getPosPref: getPosPref,
    setPosPref: setPosPref,
    resume: resume,
    attach: attach,
  };
})(typeof window !== "undefined" ? window : globalThis);
