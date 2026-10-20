/* ---------------------------------------------------------------------------
 * nav-auth.js - one shared "who am I" header state for every page.
 *
 * Any element can opt in with:
 *   data-mb-show="guest"  -> visible only when signed OUT
 *   data-mb-show="user"   -> visible only when signed IN
 *   data-mb-name          -> filled with the user's name (or email)
 *   data-mb-logout        -> click clears the session and goes home
 *
 * Requires config.js + auth.js. If those failed to load, this file does
 * nothing and the page keeps its static markup (graceful degradation).
 *
 * Elements marked data-mb-show="user" should carry the HTML `hidden`
 * attribute so signed-out visitors never see them flash first.
 * --------------------------------------------------------------------------- */
(function () {
  'use strict';

  if (typeof window.MBAuth === 'undefined') return;

  function apply(state, user) {
    var els = document.querySelectorAll('[data-mb-show]');
    for (var i = 0; i < els.length; i++) {
      els[i].hidden = els[i].getAttribute('data-mb-show') !== state;
    }
    if (user) {
      var label = user.name || user.email || 'traveller';
      var names = document.querySelectorAll('[data-mb-name]');
      for (var j = 0; j < names.length; j++) names[j].textContent = label;
    }
  }

  function hideAll() {
    var els = document.querySelectorAll('[data-mb-show]');
    for (var i = 0; i < els.length; i++) els[i].hidden = true;
  }

  function init() {
    var els = document.querySelectorAll('[data-mb-logout]');
    for (var i = 0; i < els.length; i++) {
      els[i].addEventListener('click', function (e) {
        e.preventDefault();
        MBAuth.logout('index.html');
      });
    }

    if (!MBAuth.getToken()) {
      apply('guest', null);
      return;
    }
    // Token present: hide BOTH states for a beat instead of flashing
    // "Login" at a signed-in user, then confirm with the server.
    hideAll();
    MBAuth.me().then(function (user) {
      apply(user ? 'user' : 'guest', user);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
