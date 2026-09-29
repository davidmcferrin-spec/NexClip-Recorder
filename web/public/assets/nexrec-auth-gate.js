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

  function whepConnect(videoEl, path) {
    return api("whep_jwt", { path: path }).then(function (sess) {
      var url = sess.whep_url;
      if (!url || !global.RTCPeerConnection) {
        return sess;
      }
      var pc = new RTCPeerConnection({ iceServers: sess.ice_servers || [] });
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
      pc.ontrack = function (ev) {
        if (videoEl) videoEl.srcObject = ev.streams[0];
      };
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
          return res.text();
        }).then(function (sdp) {
          return pc.setRemoteDescription({ type: "answer", sdp: sdp });
        }).then(function () { return sess; });
      }).catch(function () { return sess; });
    });
  }

  global.NexRecAuth = {
    api: api,
    recApi: recApi,
    me: me,
    requirePage: requirePage,
    noteExportReceived: noteExportReceived,
    refreshExports: refreshExports,
    whepConnect: whepConnect,
    currentUser: function () { return _user; },
  };
})(window);
