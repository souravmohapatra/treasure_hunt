/* Treasure Hunt - Client-side helpers
 * - Theme toggle (persisted in localStorage)
 * - Hint countdown (uses HINT_DELAY_SECONDS from template)
 * - Progress bar & elapsed timer on clue pages
 * - Small fetch-based auto-refresh utility
 *
 * No external dependencies; vanilla JS only.
 */
(function () {
  'use strict';

  // ---------------------------
  // Utilities
  // ---------------------------
  function clamp(num, min, max) {
    return Math.min(Math.max(num, min), max);
  }

  function pad2(n) {
    return n < 10 ? '0' + n : '' + n;
  }

  function formatMMSS(totalSeconds) {
    totalSeconds = Math.max(0, Math.floor(totalSeconds));
    var m = Math.floor(totalSeconds / 60);
    var s = totalSeconds % 60;
    return pad2(m) + ':' + pad2(s);
  }

  // ---------------------------
  // Theme (Light/Dark)
  // ---------------------------
  var THEME_STORAGE_KEY = 'theme';

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function storeTheme(theme) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch (_) {
      // ignore
    }
  }

  function systemPrefersDark() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function applyTheme(theme) {
    var body = document.body;
    if (!body) return;
    var t = theme || (getStoredTheme() || (systemPrefersDark() ? 'dark' : 'light'));
    body.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
  }

  function toggleTheme() {
    var current = document.body.getAttribute('data-theme') || 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    storeTheme(next);
  }

  // ---------------------------
  // Clue page: progress bar
  // ---------------------------
  function initProgressBar() {
    var container = document.getElementById('huntProgress');
    if (!container) return;
    var current = parseInt(container.getAttribute('data-current') || '0', 10);
    var total = parseInt(container.getAttribute('data-total') || '0', 10);
    if (!total || total <= 0) return;
    var pct = clamp((current / total) * 100, 0, 100);
    var bar = container.querySelector('.progress-bar');
    if (bar) {
      bar.style.width = pct.toFixed(0) + '%';
      bar.setAttribute('aria-valuenow', String(current));
    }
  }

  // ---------------------------
  // Clue page: elapsed timer (client-only)
  // ---------------------------
  var HUNT_START_KEY = 'huntStartAt';

  function initElapsedTimer() {
    var el = document.getElementById('elapsedTimer');
    if (!el) return;

    var start = 0;
    try {
      var stored = localStorage.getItem(HUNT_START_KEY);
      if (!stored) {
        start = Date.now();
        localStorage.setItem(HUNT_START_KEY, String(start));
      } else {
        start = parseInt(stored, 10);
        if (isNaN(start)) {
          start = Date.now();
          localStorage.setItem(HUNT_START_KEY, String(start));
        }
      }
    } catch (_) {
      start = Date.now();
    }

    function tick() {
      var diffSec = Math.floor((Date.now() - start) / 1000);
      el.textContent = formatMMSS(diffSec);
    }

    tick();
    setInterval(tick, 1000);
  }

  // ---------------------------
  // Clue page: hint countdown
  // ---------------------------
  function initHintCountdown() {
    var btn = document.getElementById('hintBtn');
    if (!btn) return;
    // Only run countdown if the button is disabled (i.e., hint not yet available)
    if (!btn.disabled) return;

    var delay = parseInt(btn.getAttribute('data-hint-delay') || '0', 10);
    if (!delay || delay <= 0) {
      // no delay, enable immediately
      btn.disabled = false;
      var lbl0 = document.getElementById('hintBtnLabel');
      if (lbl0) lbl0.textContent = 'Use Hint';
      return;
    }

    var label = document.getElementById('hintBtnLabel');
    var countdownSpan = document.getElementById('hintCountdown');
    var remaining = delay;

    function updateLabel() {
      if (!label) return;
      if (remaining <= 0) {
        label.textContent = 'Use Hint';
      } else if (countdownSpan) {
        countdownSpan.textContent = String(remaining);
      } else {
        // Fallback if span missing
        label.textContent = 'Hint (available in ' + remaining + ' s)';
      }
    }

    function tick() {
      remaining -= 1;
      if (remaining <= 0) {
        btn.disabled = false;
        updateLabel();
        clearInterval(timerId);
      } else {
        updateLabel();
      }
    }

    updateLabel();
    var timerId = setInterval(tick, 1000);
  }

  // ---------------------------
  // Small fetch-based auto-refresh helper
  // ---------------------------
  function refreshElementViaFetch(sourceUrl, sourceSelector, targetSelector) {
    try {
      fetch(sourceUrl, { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (res) { return res.ok ? res.text() : null; })
        .then(function (html) {
          if (!html) return;
          var parser = new DOMParser();
          var doc = parser.parseFromString(html, 'text/html');
          var fresh = doc.querySelector(sourceSelector);
          var target = document.querySelector(targetSelector);
          if (fresh && target) {
            target.innerHTML = fresh.innerHTML;
          }
        })
        .catch(function () { /* ignore transient errors */ });
    } catch (_) {
      // ignore
    }
  }

  // ---------------------------
  // Boot
  // ---------------------------
  document.addEventListener('DOMContentLoaded', function () {
    // Theme init & toggle
    applyTheme(); // applies stored/system theme
    var themeBtn = document.getElementById('themeToggle');
    if (themeBtn) {
      themeBtn.addEventListener('click', function () {
        toggleTheme();
      });
    }

    // Clue page enhancements
    initProgressBar();
    initElapsedTimer();
    initHintCountdown();
  });

  // Expose a tiny API (optional)
  window.TreasureHunt = {
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    startHintCountdown: initHintCountdown,
    startElapsedTimer: initElapsedTimer,
    refreshElementViaFetch: refreshElementViaFetch,
    formatMMSS: formatMMSS
  };
})();
