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
      return user;
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
    whepConnect: whepConnect,
    currentUser: function () { return _user; },
  };
})(window);
