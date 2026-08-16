/* ============================================================
   boot.js — picks the data source, then starts the dashboard.

   Without this you have to edit index.html by hand on the gateway Pi to
   swap data-source.js for data-source.live.js — and that edit then
   conflicts on every `git pull`, forever. Instead, ask the server.

   Rule:
     /api/site answers   ->  we are being served BY the gateway  ->  live
     it does not         ->  GitHub Pages, file://, a laptop     ->  simulated

   Override with a query string when you need to force one:
     ?live   always use the gateway   (fails visibly if unreachable)
     ?sim    always use the simulator (useful for demoing on the Pi itself)
   ============================================================ */

(function () {
  'use strict';

  const PROBE_TIMEOUT_MS = 2500;
  const params = new URLSearchParams(window.location.search);
  const forced = params.has('live') ? 'live'
               : params.has('sim') ? 'sim' : null;

  function load(src) {
    return new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = src;
      el.onload = resolve;
      el.onerror = () => reject(new Error('failed to load ' + src));
      document.head.appendChild(el);
    });
  }

  // A HEAD-style probe with a hard timeout. A gateway that is up answers in
  // milliseconds on a LAN; anything slower is almost certainly not a gateway.
  function gatewayIsServingUs() {
    if (forced === 'live') return Promise.resolve(true);
    if (forced === 'sim') return Promise.resolve(false);
    if (window.location.protocol === 'file:') return Promise.resolve(false);

    return new Promise(resolve => {
      const timer = setTimeout(() => resolve(false), PROBE_TIMEOUT_MS);
      fetch('/api/site', { cache: 'no-store' })
        .then(r => { clearTimeout(timer); resolve(r.ok); })
        .catch(() => { clearTimeout(timer); resolve(false); });
    });
  }

  function announce(mode, why) {
    console.log('[boot] data source: %s (%s)', mode, why);
    const label = document.getElementById('srcLabel');
    if (label) label.textContent = mode === 'live' ? 'gateway Pi (live)' : 'simulated';
  }

  // app.js registers on DOMContentLoaded. By the time the probe resolves that
  // has usually already fired, so dispatch it again once the data source is in
  // place — app.js only reads DataSource inside that handler.
  function startApp() {
    return load('app.js').then(() => {
      document.dispatchEvent(new Event('DOMContentLoaded'));
    });
  }

  load('routing.js')
    .catch(() => console.warn('[boot] routing.js unavailable — no topology view'))
    .then(gatewayIsServingUs)
    .then(live => {
      const src = live ? 'data-source.live.js' : 'data-source.js';
      return load(src).then(() => announce(
        live ? 'live' : 'simulated',
        forced ? 'forced by ?' + forced
               : live ? '/api/site answered'
                      : 'no gateway API at this origin'));
    })
    .then(startApp)
    .catch(err => {
      console.error('[boot] ' + err.message);
      const el = document.getElementById('alertDetail');
      if (el) el.textContent = 'Dashboard failed to start: ' + err.message;
    });
})();
