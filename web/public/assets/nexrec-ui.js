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
  global.NexRecUI = { getTheme: getTheme, setTheme: setTheme, toggleTheme: toggleTheme };
})(typeof window !== "undefined" ? window : globalThis);
