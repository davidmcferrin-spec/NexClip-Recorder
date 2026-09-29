/**
 * Shared theme + version badge. Load in <head> (sync) to avoid flash.
 * localStorage key: nexrec-theme ("dark" | "light"); default dark.
 */
(function (global) {
  "use strict";
  var STORAGE_KEY = "nexrec-theme";

  function normalize(v) { return v === "light" ? "light" : "dark"; }
  function read() {
    try { return normalize(global.localStorage.getItem(STORAGE_KEY)); }
    catch (e) { return "dark"; }
  }
  function apply(theme) {
    theme = normalize(theme);
    var root = global.document.documentElement;
    if (root) root.setAttribute("data-theme", theme);
    return theme;
  }
  function setTheme(theme) {
    theme = apply(theme);
    try { global.localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}
    syncToggle();
    return theme;
  }
  function getTheme() {
    var root = global.document.documentElement;
    if (root && root.getAttribute("data-theme")) {
      return normalize(root.getAttribute("data-theme"));
    }
    return read();
  }
  function toggleTheme() {
    return setTheme(getTheme() === "light" ? "dark" : "light");
  }
  function syncToggle() {
    var btn = global.document.getElementById("theme-toggle");
    if (!btn) return;
    var isLight = getTheme() === "light";
    btn.textContent = isLight ? "Dark" : "Light";
    btn.setAttribute("aria-pressed", isLight ? "true" : "false");
  }
  function paintVersion(data) {
    var el = global.document.getElementById("nav-version");
    if (!el || !data || !data.ok) return;
    el.textContent = "v" + (data.version || "0.0.0");
    el.hidden = false;
  }
  function onReady(fn) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", fn);
    } else fn();
  }
  apply(read());
  onReady(function () {
    syncToggle();
    var btn = global.document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", function () { toggleTheme(); });
    if (typeof global.fetch === "function") {
      global.fetch("/api/version", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(paintVersion)
        .catch(function () {});
    }
  });
  var DISPLAY_TZ = "America/New_York";

  function formatStation(value, withDate) {
    if (value == null || value === "") return "—";
    var d = value instanceof Date ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    var parts = new Intl.DateTimeFormat("en-US", {
      timeZone: DISPLAY_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
      timeZoneName: "short",
    }).formatToParts(d);
    var g = {};
    parts.forEach(function (p) {
      if (p.type !== "literal") g[p.type] = p.value;
    });
    var clock = g.hour + ":" + g.minute + ":" + g.second + " " + (g.timeZoneName || "ET");
    if (!withDate) return clock;
    return g.year + "-" + g.month + "-" + g.day + " " + clock;
  }

  function stationStamp(value) {
    var full = formatStation(value, true);
    if (full === "—") return "";
    return full.replace(/ [A-Za-z0-9+\-]+$/, "");
  }

  function wallPartsUtc(ms, timeZone) {
    var dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    var g = {};
    dtf.formatToParts(new Date(ms)).forEach(function (p) {
      if (p.type !== "literal") g[p.type] = p.value;
    });
    var hour = Number(g.hour);
    if (hour === 24) hour = 0;
    return Date.UTC(Number(g.year), Number(g.month) - 1, Number(g.day), hour, Number(g.minute), Number(g.second));
  }

  function parseStation(text) {
    var m = String(text == null ? "" : text).trim().match(
      /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/
    );
    if (!m) return NaN;
    var y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    var h = Number(m[4]), mi = Number(m[5]), s = Number(m[6] || 0);
    if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) return NaN;
    var utc = Date.UTC(y, mo - 1, d, h, mi, s);
    var offset = wallPartsUtc(utc, DISPLAY_TZ) - utc;
    var result = utc - offset;
    var offset2 = wallPartsUtc(result, DISPLAY_TZ) - result;
    return utc - offset2;
  }

  var PANE_MAX = 6;

  // saved null → first inputs in list order (API returns name order).
  // A saved array is the user's pane assignment; unknown or duplicate ids become blank.
  function paneSlotIds(inputs, saved) {
    var known = {};
    (inputs || []).forEach(function (inp) {
      if (inp && inp.id != null && inp.id !== "") known[String(inp.id)] = true;
    });
    if (!Array.isArray(saved)) {
      var fresh = [];
      var filled = {};
      (inputs || []).forEach(function (inp) {
        if (!inp || inp.id == null || inp.id === "" || fresh.length >= PANE_MAX) return;
        var id = String(inp.id);
        if (filled[id]) return;
        filled[id] = true;
        fresh.push(id);
      });
      while (fresh.length < PANE_MAX) fresh.push("");
      return fresh;
    }
    var ids = [];
    var seen = {};
    for (var i = 0; i < PANE_MAX; i++) {
      var id = saved[i] == null ? "" : String(saved[i]);
      if (id && known[id] && !seen[id]) {
        seen[id] = true;
        ids.push(id);
      } else {
        ids.push("");
      }
    }
    return ids;
  }

  // Picking an input that already occupies another pane swaps the two.
  function paneAssign(slotIds, paneIndex, inputId) {
    var source = Array.isArray(slotIds) ? slotIds : [];
    var next = [];
    for (var i = 0; i < PANE_MAX; i++) {
      next.push(source[i] == null ? "" : String(source[i]));
    }
    var id = inputId == null ? "" : String(inputId);
    if (paneIndex < 0 || paneIndex >= PANE_MAX) return next;
    if (!id) {
      next[paneIndex] = "";
      return next;
    }
    var other = next.indexOf(id);
    if (other >= 0 && other !== paneIndex) {
      var prev = next[paneIndex] || "";
      next[paneIndex] = id;
      next[other] = prev;
      return next;
    }
    next[paneIndex] = id;
    return next;
  }

  function paneInputs(inputs, slotIds, n) {
    var byId = {};
    (inputs || []).forEach(function (inp) {
      if (inp && inp.id != null && inp.id !== "") byId[String(inp.id)] = inp;
    });
    var count = n > PANE_MAX ? PANE_MAX : n;
    var out = [];
    for (var i = 0; i < count; i++) {
      var id = slotIds && slotIds[i] ? String(slotIds[i]) : "";
      out.push(id && byId[id] ? byId[id] : null);
    }
    return out;
  }

  function readPaneSlots(storage, key) {
    try {
      var raw = storage.getItem(key);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function writePaneSlots(storage, key, ids) {
    try { storage.setItem(key, JSON.stringify(ids)); } catch (e) {}
  }

  function coverageSpans(chunks, t0, t1) {
    var span = Math.max(1, t1 - t0);
    var out = [];
    (chunks || []).forEach(function (c) {
      var a = Date.parse(c.start_at);
      var b = Date.parse(c.end_at || "");
      if (!isFinite(b) && c.duration_s != null && isFinite(a)) b = a + Number(c.duration_s) * 1000;
      if (!isFinite(a) || !isFinite(b) || b <= a) return;
      if (b <= t0 || a >= t1) return;
      var left = ((Math.max(a, t0) - t0) / span) * 100;
      var width = ((Math.min(b, t1) - Math.max(a, t0)) / span) * 100;
      if (width <= 0) return;
      out.push({ left: left, width: width });
    });
    return out;
  }

  function exportDurationMs(job) {
    var a = Date.parse(job && job.t_in);
    var b = Date.parse(job && job.t_out);
    if (!isFinite(a) || !isFinite(b) || b <= a) return 0;
    return b - a;
  }

  function exportRate(job) {
    if (job && job.encode_mode === "copy") return 15;
    if (job && job.encode_mode === "encode") return 1;
    if (job && job.quality === "proxy") return 1;
    return 8;
  }

  function exportRemainingMs(job, now) {
    if (!job) return 0;
    var status = job.status;
    if (status === "done" || status === "error" || status === "cancelled") return 0;
    var dur = exportDurationMs(job);
    if (status === "running") {
      var pct = Number(job.progress_pct);
      var started = Date.parse(job.started_at || "");
      if (pct > 1 && pct < 100 && isFinite(started) && now > started) {
        return Math.max(0, (now - started) * (100 - pct) / pct);
      }
      return dur / exportRate(job);
    }
    return dur / exportRate(job);
  }

  function exportQueueEta(jobs, job, now) {
    var active = (jobs || []).filter(function (row) {
      return row && (row.status === "running" || row.status === "queued");
    });
    active.sort(function (a, b) {
      if (a.status !== b.status) return a.status === "running" ? -1 : 1;
      return String(a.created_at || "").localeCompare(String(b.created_at || ""));
    });
    var total = 0;
    for (var i = 0; i < active.length; i++) {
      total += exportRemainingMs(active[i], now);
      if (active[i].id === job.id) return total;
    }
    return 0;
  }

  function formatEta(ms) {
    if (ms == null || !isFinite(ms) || ms < 1000) return "—";
    var s = Math.round(ms / 1000);
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    s = s % 60;
    if (m < 60) return m + "m " + s + "s";
    var h = Math.floor(m / 60);
    m = m % 60;
    return h + "h " + m + "m";
  }

  global.NexRecUI = {
    getTheme: getTheme,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
    timeZone: DISPLAY_TZ,
    formatStation: formatStation,
    stationStamp: stationStamp,
    parseStation: parseStation,
    PANE_MAX: PANE_MAX,
    paneSlotIds: paneSlotIds,
    paneAssign: paneAssign,
    paneInputs: paneInputs,
    readPaneSlots: readPaneSlots,
    writePaneSlots: writePaneSlots,
    coverageSpans: coverageSpans,
    exportDurationMs: exportDurationMs,
    exportRemainingMs: exportRemainingMs,
    exportQueueEta: exportQueueEta,
    formatEta: formatEta,
  };
})(typeof window !== "undefined" ? window : globalThis);
