/**
 * nexrec-cc.js — 608/708 caption overlay on a Live or Export pane.
 *
 * The browser does not paint CEA-608 out of the picture. This draws the
 * extracted caption cues (kind=caption) for the playhead time.
 * Live cues exist only after the chunk is indexed.
 *
 * Pref: nexrec-cc-on  1 | 0  (default off)
 */
(function (global) {
  "use strict";

  const PREF_ON = "nexrec-cc-on";

  function prefOn() {
    try {
      return localStorage.getItem(PREF_ON) === "1";
    } catch (e) {
      return false;
    }
  }

  function setPrefOn(on) {
    try {
      localStorage.setItem(PREF_ON, on ? "1" : "0");
    } catch (e) { /* private mode */ }
  }

  function parseMs(value) {
    if (value == null || value === "") return NaN;
    const n = Date.parse(value);
    return Number.isFinite(n) ? n : NaN;
  }

  /**
   * Caption lines whose window covers ms. A missing end holds for 2 seconds.
   * @param {Array<{t_start:string,t_end?:string,text:string}>} cues
   * @param {number} ms
   * @returns {string[]}
   */
  function linesAt(cues, ms) {
    const lines = [];
    if (!cues || !Number.isFinite(ms)) return lines;
    for (let i = 0; i < cues.length; i++) {
      const cue = cues[i];
      if (!cue || !cue.text) continue;
      const start = parseMs(cue.t_start);
      if (!Number.isFinite(start) || ms < start) continue;
      let end = parseMs(cue.t_end);
      if (!Number.isFinite(end) || end <= start) end = start + 2000;
      if (ms < end) lines.push(String(cue.text));
    }
    return lines;
  }

  function attach(opts) {
    const container = opts && opts.container;
    if (!container) {
      return {
        setTime: function () {},
        setCues: function () {},
        setVisible: function () {},
        destroy: function () {},
        isVisible: function () { return false; },
      };
    }
    const root = document.createElement("div");
    root.className = "nexrec-cc";
    root.hidden = true;
    const text = document.createElement("div");
    text.className = "nexrec-cc-text";
    root.appendChild(text);
    container.appendChild(root);

    let cues = [];
    let at = NaN;
    let visible = false;

    function paint() {
      if (!visible) {
        root.hidden = true;
        text.textContent = "";
        return;
      }
      const lines = linesAt(cues, at);
      if (!lines.length) {
        root.hidden = true;
        text.textContent = "";
        return;
      }
      text.textContent = lines.join("\n");
      root.hidden = false;
    }

    return {
      setCues: function (next) {
        cues = next || [];
        paint();
      },
      setTime: function (ms) {
        at = ms;
        paint();
      },
      setVisible: function (on) {
        visible = !!on;
        setPrefOn(visible);
        paint();
      },
      isVisible: function () { return visible; },
      destroy: function () {
        if (root.parentNode) root.parentNode.removeChild(root);
      },
    };
  }

  global.NexRecCc = {
    PREF_ON: PREF_ON,
    getOnPref: prefOn,
    setOnPref: setPrefOn,
    linesAt: linesAt,
    attach: attach,
  };
})(window);
