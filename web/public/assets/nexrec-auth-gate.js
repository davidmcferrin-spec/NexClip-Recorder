/**
 * Session gate + /api/auth helper (NexVUE-style).
 */
(function (global) {
  "use strict";
  var AUTH_URL = "/api/auth";
  var _user = null;

  function api(action, body) {
    var opts = {
      method: body ? "POST" : "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {},
    };
    var url = AUTH_URL + "?action=" + encodeURIComponent(action);
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(Object.assign({ action: action }, body));
    }
    return fetch(url, opts).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || !data || data.ok === false) {
          var err = new Error((data && data.error) || ("HTTP " + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function recApi(action, body) {
    var opts = {
      method: body ? "POST" : "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {},
    };
    var url = "/api/recorder?action=" + encodeURIComponent(action);
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(Object.assign({ action: action }, body));
    }
    return fetch(url, opts).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || !data || data.ok === false) {
          var err = new Error((data && data.error) || ("HTTP " + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function me() {
    return api("me").then(function (data) {
      _user = data.user || null;
      return _user;
    });
  }

  function requirePage(opts) {
    opts = opts || {};
    var roles = opts.roles || null;
    var next = global.location.pathname + global.location.search;
    return me().catch(function () { return null; }).then(function (user) {
      if (!user) {
        global.location.href = "/login?next=" + encodeURIComponent(next);
        return Promise.reject(new Error("unauthorized"));
      }
      if (user.must_change_password) {
        global.location.href = "/login?change=1&next=" + encodeURIComponent(next);
        return Promise.reject(new Error("must_change_password"));
      }
      if (roles && roles.length && roles.indexOf(user.role) < 0) {
        global.location.href = "/live";
        return Promise.reject(new Error("forbidden"));
      }
      applyNav(user);
      startExportWatch(user);
      startSpaceWarn();
      return user;
    });
  }

  var exportWatchStarted = false;
  var WATCH_KEY = "nexrec-export-watch";

  function loadWatch() {
    try {
      var raw = global.sessionStorage.getItem(WATCH_KEY);
      var data = raw ? JSON.parse(raw) : null;
      if (!data || typeof data !== "object") throw new Error("empty");
      data.seen = data.seen || {};
      data.notices = data.notices || [];
      return data;
    } catch (e) {
      return { seen: {}, notices: [], primed: false };
    }
  }

  function saveWatch(data) {
    try { global.sessionStorage.setItem(WATCH_KEY, JSON.stringify(data)); } catch (e) {}
  }

  function pushNotice(data, notice) {
    var dup = data.notices.some(function (n) { return n.id === notice.id && n.kind === notice.kind; });
    if (dup) return;
    notice.read = false;
    data.notices.unshift(notice);
    data.notices = data.notices.slice(0, 12);
  }

  function escNotice(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function paintNotices() {
    var data = loadWatch();
    var badge = document.getElementById("notice-badge");
    var panel = document.getElementById("notice-panel");
    var unread = data.notices.filter(function (n) { return !n.read; }).length;
    if (badge) {
      badge.hidden = unread < 1;
      badge.textContent = String(unread);
    }
    if (!panel) return;
    if (!data.notices.length) {
      panel.innerHTML = "<p class=\"sub\">No export notices yet.</p>";
      return;
    }
    panel.innerHTML = data.notices.map(function (n) {
      var status = "<a class=\"notice-link\" href=\"" + escNotice(n.href) + "\">status</a>";
      var dl = n.download ? " <a class=\"notice-link\" href=\"" + escNotice(n.download) + "\">download</a>" : "";
      return "<p>" + escNotice(n.text) + "<br>" + status + dl + "</p>";
    }).join("");
  }

  function mountNotices() {
    if (document.getElementById("nav-bell")) return;
    var nav = document.querySelector("nav.topnav");
    if (!nav) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-toggle nav-bell";
    btn.id = "nav-bell";
    btn.textContent = "Exports";
    var badge = document.createElement("span");
    badge.id = "notice-badge";
    badge.className = "notice-badge";
    badge.hidden = true;
    btn.appendChild(badge);
    var panel = document.createElement("div");
    panel.id = "notice-panel";
    panel.className = "notice-panel";
    panel.hidden = true;
    var anchor = document.getElementById("theme-toggle");
    if (anchor) nav.insertBefore(panel, anchor);
    else nav.appendChild(panel);
    if (anchor) nav.insertBefore(btn, panel);
    else nav.appendChild(btn);
    btn.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open;
      if (open) {
        var data = loadWatch();
        data.notices.forEach(function (n) { n.read = true; });
        saveWatch(data);
        paintNotices();
      }
    });
  }

  function noteFromTransition(data, job, prev) {
    var href = "/export#" + encodeURIComponent(job.id);
    var status = job.status;
    if ((status === "queued" || status === "running" || status === "done" || status === "error") && prev == null && status !== "queued") {
      pushNotice(data, { id: job.id, kind: "received", text: "Export " + job.id + " received.", href: href });
    }
    if (status === "queued" && prev == null) {
      pushNotice(data, { id: job.id, kind: "received", text: "Export " + job.id + " received.", href: href });
    }
    if ((status === "running" || status === "done") && prev !== "running" && prev !== "done") {
      pushNotice(data, { id: job.id, kind: "started", text: "Export " + job.id + " started.", href: href });
    }
    if (status === "done" && prev !== "done") {
      pushNotice(data, {
        id: job.id,
        kind: "done",
        text: "Export " + job.id + " is ready.",
        href: href,
        download: "/api/exports/" + encodeURIComponent(job.id) + "/file",
      });
    }
    if (status === "error" && prev !== "error") {
      pushNotice(data, { id: job.id, kind: "error", text: "Export " + job.id + " failed.", href: href });
    }
  }

  function applyExportList(jobs, user) {
    var data = loadWatch();
    var mine = (jobs || []).filter(function (job) { return job.created_by === user.username; });
    if (!data.primed) {
      mine.forEach(function (job) {
        if (!data.seen[job.id]) data.seen[job.id] = job.status;
      });
      data.primed = true;
    } else {
      mine.forEach(function (job) {
        var prev = data.seen[job.id];
        if (prev !== job.status) noteFromTransition(data, job, prev);
        data.seen[job.id] = job.status;
      });
    }
    saveWatch(data);
    paintNotices();
    try {
      global.dispatchEvent(new CustomEvent("nexrec-exports", { detail: jobs || [] }));
    } catch (e) {}
    var busy = (jobs || []).some(function (job) {
      return job.status === "queued" || job.status === "running";
    });
    return busy;
  }

  var spaceWarnStarted = false;

  function startSpaceWarn() {
    if (spaceWarnStarted) return;
    spaceWarnStarted = true;
    function paint(data) {
      var bar = document.getElementById("space-warn");
      if (!data || !data.warn || !data.message) {
        if (bar) bar.hidden = true;
        return;
      }
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "space-warn";
        bar.className = "space-warn";
        bar.setAttribute("role", "status");
        var nav = document.querySelector("nav.topnav");
        if (nav && nav.parentNode) nav.parentNode.insertBefore(bar, nav.nextSibling);
        else document.body.insertBefore(bar, document.body.firstChild);
      }
      bar.hidden = false;
      bar.textContent = data.message;
    }
    function tick() {
      recApi("storage_forecast").then(function (data) {
        paint(data);
        global.setTimeout(tick, 60000);
      }).catch(function () {
        global.setTimeout(tick, 60000);
      });
    }
    tick();
  }

  function startExportWatch(user) {
    mountNotices();
    paintNotices();
    if (exportWatchStarted || !user) return;
    exportWatchStarted = true;
    function tick() {
      recApi("exports_list").then(function (data) {
        var busy = applyExportList(data.exports || [], user);
        global.setTimeout(tick, busy ? 3000 : 15000);
      }).catch(function () {
        global.setTimeout(tick, 15000);
      });
    }
    tick();
  }

  function noteExportReceived(id) {
    var data = loadWatch();
    data.seen[id] = "queued";
    data.primed = true;
    pushNotice(data, { id: id, kind: "received", text: "Export " + id + " received.", href: "/export#" + encodeURIComponent(id) });
    saveWatch(data);
    mountNotices();
    paintNotices();
  }

  function refreshExports() {
    return recApi("exports_list").then(function (data) {
      try {
        global.dispatchEvent(new CustomEvent("nexrec-exports", { detail: data.exports || [] }));
      } catch (e) {}
      return data;
    });
  }

  function applyNav(user) {
    document.querySelectorAll("[data-auth-role]").forEach(function (el) {
      var need = (el.getAttribute("data-auth-role") || "").split(",").map(function (s) {
        return s.trim();
      }).filter(Boolean);
      el.hidden = need.indexOf(user.role) < 0;
    });
    var logout = document.getElementById("nav-logout");
    if (logout) {
      logout.hidden = false;
      logout.onclick = function (ev) {
        ev.preventDefault();
        api("logout", {}).finally(function () {
          global.location.href = "/login";
        });
      };
    }
    var who = document.getElementById("nav-who");
    if (who && user && user.username) {
      who.textContent = user.username + " (" + user.role + ")";
      who.hidden = false;
    }
  }

  var WHEP_STALL_MS = 9000;
  var WHEP_WATCH_MS = 3000;
  var WHEP_DISC_GRACE_MS = 4000;
  var WHEP_RETRY_BASE_MS = 1000;
  var WHEP_RETRY_MAX_MS = 15000;

  function whepState(videoEl) {
    return videoEl && videoEl._nexrecWhep ? videoEl._nexrecWhep : null;
  }

  function whepClearMedia(videoEl) {
    if (!videoEl) return;
    var stream = videoEl.srcObject;
    if (stream && stream.getTracks) {
      try {
        stream.getTracks().forEach(function (t) {
          try { t.stop(); } catch (e) {}
        });
      } catch (e) {}
    }
    try { videoEl.srcObject = null; } catch (e) {}
  }

  function whepDeleteSession(url) {
    if (!url) return;
    try {
      fetch(url, { method: "DELETE", credentials: "omit", cache: "no-store" }).catch(function () {});
    } catch (e) {}
  }

  function whepTearPc(st) {
    if (!st) return;
    if (st.discTimer) {
      clearTimeout(st.discTimer);
      st.discTimer = null;
    }
    if (st.watchTimer) {
      clearInterval(st.watchTimer);
      st.watchTimer = null;
    }
    if (st.pc) {
      try { st.pc.ontrack = null; } catch (e) {}
      try { st.pc.onconnectionstatechange = null; } catch (e) {}
      try { st.pc.oniceconnectionstatechange = null; } catch (e) {}
      try { st.pc.close(); } catch (e) {}
      st.pc = null;
    }
    whepDeleteSession(st.sessionUrl);
    st.sessionUrl = null;
    st.lastFrames = 0;
    st.lastFrameAt = 0;
    st.gotFrame = false;
  }

  function whepClose(videoEl) {
    var st = whepState(videoEl);
    if (!st) {
      whepClearMedia(videoEl);
      return;
    }
    st.closed = true;
    if (st.retryTimer) {
      clearTimeout(st.retryTimer);
      st.retryTimer = null;
    }
    if (st._onVis) {
      try { document.removeEventListener("visibilitychange", st._onVis); } catch (e) {}
      st._onVis = null;
    }
    if (st._onPageShow) {
      try { global.removeEventListener("pageshow", st._onPageShow); } catch (e) {}
      st._onPageShow = null;
    }
    if (st._onResume) {
      try { global.removeEventListener("resume", st._onResume); } catch (e) {}
      st._onResume = null;
    }
    whepTearPc(st);
    whepClearMedia(videoEl);
    try { videoEl._nexrecWhep = null; } catch (e) {}
    try {
      videoEl.dispatchEvent(new CustomEvent("nexrec-whep-state", { detail: { state: "closed" } }));
    } catch (e) {}
  }

  function whepEmit(videoEl, state, detail) {
    try {
      videoEl.dispatchEvent(new CustomEvent("nexrec-whep-state", {
        detail: Object.assign({ state: state }, detail || {}),
      }));
    } catch (e) {}
  }

  function whepBackoff(attempt) {
    var exp = Math.min(WHEP_RETRY_MAX_MS, WHEP_RETRY_BASE_MS * Math.pow(2, Math.max(0, attempt)));
    return exp + Math.floor(Math.random() * 250);
  }

  function whepConnect(videoEl, path) {
    if (!videoEl || !path) return Promise.resolve(null);
    whepClose(videoEl);
    var st = {
      closed: false,
      path: path,
      pc: null,
      sessionUrl: null,
      retryTimer: null,
      watchTimer: null,
      discTimer: null,
      attempt: 0,
      lastFrames: 0,
      lastFrameAt: 0,
      gotFrame: false,
      hiddenAt: 0,
      sess: null,
      connecting: false,
    };
    videoEl._nexrecWhep = st;

    function scheduleRetry(reason) {
      if (st.closed) return;
      if (st.retryTimer) return;
      var delay = whepBackoff(st.attempt);
      st.attempt += 1;
      whepEmit(videoEl, "reconnecting", { reason: reason || "retry", attempt: st.attempt, delay: delay });
      st.retryTimer = setTimeout(function () {
        st.retryTimer = null;
        connectOnce();
      }, delay);
    }

    function recover(reason) {
      if (st.closed || st.connecting) return;
      whepTearPc(st);
      whepClearMedia(videoEl);
      scheduleRetry(reason);
    }

    function armWatchdog(pc) {
      if (st.watchTimer) clearInterval(st.watchTimer);
      st.watchTimer = setInterval(function () {
        if (st.closed || st.pc !== pc) return;
        var cs = pc.connectionState;
        var ice = pc.iceConnectionState;
        if (cs === "failed" || ice === "failed") {
          recover("failed");
          return;
        }
        if (typeof pc.getStats !== "function") return;
        pc.getStats().then(function (report) {
          if (st.closed || st.pc !== pc) return;
          var frames = null;
          report.forEach(function (row) {
            if (row && row.type === "inbound-rtp" && (row.kind === "video" || row.mediaType === "video")) {
              if (typeof row.framesDecoded === "number") frames = row.framesDecoded;
            }
          });
          var now = Date.now();
          if (frames == null) {
            // No inbound video yet after connect — keep waiting; offer path retries on failed.
            if (st.gotFrame && st.lastFrameAt && now - st.lastFrameAt > WHEP_STALL_MS) {
              recover("silent");
            }
            return;
          }
          if (frames > st.lastFrames) {
            st.lastFrames = frames;
            st.lastFrameAt = now;
            st.gotFrame = true;
            st.attempt = 0;
            return;
          }
          if (st.gotFrame && st.lastFrameAt && now - st.lastFrameAt > WHEP_STALL_MS) {
            recover("stalled");
          }
        }).catch(function () {});
      }, WHEP_WATCH_MS);
    }

    function bindPcLifecycle(pc) {
      function onState() {
        if (st.closed || st.pc !== pc) return;
        var cs = pc.connectionState;
        var ice = pc.iceConnectionState;
        if (cs === "failed" || ice === "failed") {
          recover("failed");
          return;
        }
        if (cs === "disconnected" || ice === "disconnected") {
          if (st.discTimer) return;
          st.discTimer = setTimeout(function () {
            st.discTimer = null;
            if (st.closed || st.pc !== pc) return;
            var stillBad = pc.connectionState === "disconnected" ||
              pc.connectionState === "failed" ||
              pc.iceConnectionState === "disconnected" ||
              pc.iceConnectionState === "failed";
            if (stillBad) recover("disconnected");
          }, WHEP_DISC_GRACE_MS);
          return;
        }
        if (st.discTimer && (cs === "connected" || ice === "connected" || ice === "completed")) {
          clearTimeout(st.discTimer);
          st.discTimer = null;
        }
      }
      pc.onconnectionstatechange = onState;
      pc.oniceconnectionstatechange = onState;
    }

    function connectOnce() {
      if (st.closed) return Promise.resolve(null);
      if (st.connecting) return Promise.resolve(st.sess);
      st.connecting = true;
      whepEmit(videoEl, "connecting", { attempt: st.attempt, path: path });
      return api("whep_jwt", { path: path }).then(function (sess) {
        st.sess = sess;
        var url = sess.whep_url;
        if (st.closed) return sess;
        if (!url || !global.RTCPeerConnection) {
          st.connecting = false;
          whepEmit(videoEl, "unavailable", {});
          return sess;
        }
        whepTearPc(st);
        whepClearMedia(videoEl);
        var pc = new RTCPeerConnection({ iceServers: sess.ice_servers || [] });
        st.pc = pc;
        pc.addTransceiver("video", { direction: "recvonly" });
        pc.addTransceiver("audio", { direction: "recvonly" });
        // Video and audio arrive as separate tracks, often on separate streams.
        // Keeping only the last stream drops whichever track arrived first.
        pc.ontrack = function (ev) {
          if (st.closed || !videoEl || !ev.track || st.pc !== pc) return;
          var stream = videoEl.srcObject;
          if (!stream || typeof stream.addTrack !== "function" || typeof stream.getTracks !== "function") {
            stream = new MediaStream();
            videoEl.srcObject = stream;
          }
          var tracks = stream.getTracks();
          for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].id === ev.track.id) return;
          }
          stream.addTrack(ev.track);
          try {
            ev.track.addEventListener("ended", function () {
              if (st.closed || st.pc !== pc) return;
              recover("track-ended");
            });
          } catch (e) {}
          try {
            videoEl.dispatchEvent(new Event("nexrec-whep-track"));
          } catch (e) { /* the Live page also polls srcObject */ }
          whepEmit(videoEl, "track", { kind: ev.track.kind });
        };
        bindPcLifecycle(pc);
        return pc.createOffer().then(function (offer) {
          return pc.setLocalDescription(offer).then(function () { return offer; });
        }).then(function (offer) {
          var q = url.indexOf("?") >= 0 ? "&" : "?";
          var whep = sess.jwt && sess.jwt !== "local" ? url + q + "jwt=" + encodeURIComponent(sess.jwt) : url;
          return fetch(whep, {
            method: "POST",
            headers: { "Content-Type": "application/sdp" },
            body: offer.sdp,
          }).then(function (res) {
            if (!res.ok) throw new Error("WHEP " + res.status);
            var loc = res.headers && res.headers.get ? res.headers.get("Location") : null;
            if (loc) {
              try { st.sessionUrl = new URL(loc, whep).toString(); } catch (e) { st.sessionUrl = loc; }
              if (sess.jwt && sess.jwt !== "local" && st.sessionUrl.indexOf("jwt=") < 0) {
                st.sessionUrl += (st.sessionUrl.indexOf("?") >= 0 ? "&" : "?") + "jwt=" + encodeURIComponent(sess.jwt);
              }
            }
            return res.text();
          }).then(function (sdp) {
            if (st.closed || st.pc !== pc) return sess;
            return pc.setRemoteDescription({ type: "answer", sdp: sdp }).then(function () {
              armWatchdog(pc);
              st.connecting = false;
              st.lastFrameAt = Date.now();
              whepEmit(videoEl, "connected", { attempt: st.attempt });
              try { videoEl.play().catch(function () {}); } catch (e) {}
              return sess;
            });
          });
        }).catch(function (err) {
          st.connecting = false;
          if (st.closed) return sess;
          whepTearPc(st);
          whepClearMedia(videoEl);
          scheduleRetry((err && err.message) || "offer");
          return sess;
        });
      }).catch(function () {
        st.connecting = false;
        if (st.closed) return null;
        scheduleRetry("auth");
        return null;
      });
    }

    // Background tabs and idle/sleep hosts commonly freeze WHEP. On wake, prefer a
    // clean re-offer instead of a black pane that needs a manual source cycle.
    function onWake(reason) {
      if (st.closed) return;
      if (!st.pc) {
        if (!st.retryTimer && !st.connecting) scheduleRetry(reason || "wake");
        return;
      }
      var hiddenFor = st.hiddenAt ? Date.now() - st.hiddenAt : 0;
      st.hiddenAt = 0;
      if (hiddenFor >= WHEP_STALL_MS || (st.gotFrame && st.lastFrameAt && Date.now() - st.lastFrameAt > WHEP_STALL_MS)) {
        recover(reason || "wake");
      }
    }
    function onVis() {
      if (st.closed) return;
      if (document.visibilityState === "hidden") {
        st.hiddenAt = Date.now();
        return;
      }
      onWake("visible");
    }
    function onPageShow(ev) {
      if (ev && ev.persisted) onWake("pageshow");
    }
    function onResume() {
      onWake("resume");
    }
    try {
      document.addEventListener("visibilitychange", onVis);
      global.addEventListener("pageshow", onPageShow);
      global.addEventListener("resume", onResume);
      st._onVis = onVis;
      st._onPageShow = onPageShow;
      st._onResume = onResume;
    } catch (e) {}

    return connectOnce();
  }

  global.NexRecAuth = {
    api: api,
    recApi: recApi,
    me: me,
    requirePage: requirePage,
    noteExportReceived: noteExportReceived,
    refreshExports: refreshExports,
    whepConnect: whepConnect,
    whepClose: whepClose,
    currentUser: function () { return _user; },
  };
})(window);
