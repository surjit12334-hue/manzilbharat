/* ---------------------------------------------------------------------------
 * auth.js - shared session helpers for every page.
 *
 * The API signs a token on signup / login / Google login; login.html and
 * createaccount.html store it through MBAuth.setSession(). Any page that
 * needs the signed-in user loads this file and calls MBAuth.me().
 *
 * The token travels as:   Authorization: Bearer <token>
 * --------------------------------------------------------------------------- */
(function () {
  'use strict';

  var TOKEN_KEY = 'manzilToken';
  var USER_KEY = 'manzilUser';

  function storages() {
    // Read from both: a token saved with "remember me" lives in
    // localStorage, otherwise in sessionStorage. A session must be
    // found regardless of which page wrote it.
    var out = [];
    try {
      out.push(window.localStorage);
    } catch (e) {}
    try {
      out.push(window.sessionStorage);
    } catch (e) {}
    return out;
  }

  function getToken() {
    var list = storages();
    for (var i = 0; i < list.length; i++) {
      var token = null;
      try {
        token = list[i].getItem(TOKEN_KEY);
      } catch (e) {}
      if (token) return token;
    }
    return '';
  }

  // Fast, possibly stale profile for instant rendering (header greeting).
  // Always confirm with MBAuth.me() before trusting it.
  function getCachedUser() {
    var list = storages();
    for (var i = 0; i < list.length; i++) {
      var raw = null;
      try {
        raw = list[i].getItem(USER_KEY);
      } catch (e) {}
      if (raw) {
        try {
          return JSON.parse(raw);
        } catch (e) {
          return null;
        }
      }
    }
    return null;
  }

  function setSession(token, user, remember) {
    try {
      var store = remember ? window.localStorage : window.sessionStorage;
      store.setItem(TOKEN_KEY, token);
      store.setItem(USER_KEY, JSON.stringify(user || {}));
    } catch (e) {
      /* private mode: storage disabled, session won't persist */
    }
  }

  function clearSession() {
    var list = storages();
    for (var i = 0; i < list.length; i++) {
      try {
        list[i].removeItem(TOKEN_KEY);
        list[i].removeItem(USER_KEY);
      } catch (e) {}
    }
  }

  function authHeaders(extra) {
    var headers = {};
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) headers[k] = extra[k];
      }
    }
    var token = getToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
  }

  // Live profile from the server, or null when signed out / invalid.
  // An invalid token is cleared so the page stops carrying a dead session.
  async function me() {
    if (!getToken()) return null;
    try {
      var res = await fetch((window.API_BASE || '') + '/api/me', {
        headers: authHeaders(),
      });
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        clearSession();
        return null;
      }
      if (!res.ok) return null;
      var user = await res.json();
      try {
        window.sessionStorage.setItem(USER_KEY, JSON.stringify(user));
      } catch (e) {}
      return user;
    } catch (e) {
      return null; // network failure: keep the token, just report signed-out
    }
  }

  // Call at the top of pages that must not be reachable signed-out.
  function requireLogin(redirectTo) {
    if (!getToken()) {
      window.location.href = redirectTo || 'login.html';
      return false;
    }
    return true;
  }

  function logout(redirectTo) {
    clearSession();
    window.location.href = redirectTo || 'index.html';
  }

  window.MBAuth = {
    getToken: getToken,
    getCachedUser: getCachedUser,
    setSession: setSession,
    clearSession: clearSession,
    authHeaders: authHeaders,
    me: me,
    requireLogin: requireLogin,
    logout: logout,
  };
})();
