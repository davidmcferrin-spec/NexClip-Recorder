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

  global.NexRecUI = {
    getTheme: getTheme,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
    timeZone: DISPLAY_TZ,
    formatStation: formatStation,
    PANE_MAX: PANE_MAX,
    paneSlotIds: paneSlotIds,
    paneAssign: paneAssign,
    paneInputs: paneInputs,
    readPaneSlots: readPaneSlots,
    writePaneSlots: writePaneSlots,
  };
})(typeof window !== "undefined" ? window : globalThis);
