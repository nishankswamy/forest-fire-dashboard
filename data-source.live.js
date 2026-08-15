/* ============================================================
   data-source.live.js — REAL DATA from the gateway Pi.

   To switch the dashboard from simulated to live, change one line
   in index.html:

       <script src="data-source.js"></script>
       becomes
       <script src="data-source.live.js"></script>

   Nothing else in the dashboard changes — this file exposes the
   identical window.DataSource contract that app.js expects.

   If you open the dashboard by double-clicking index.html instead
   of loading it from the Pi, set API_BASE to the Pi's address:
       const API_BASE = 'http://192.168.1.42:5000';
   ============================================================ */

(function () {
  'use strict';

  // Empty string = same origin, i.e. the Pi is serving this page.
  const API_BASE = '';

  const POLL_MS = 5000;
  const HISTORY_TTL_MS = 60000;   // re-fetch a node's 7-day series at most once a minute

  let nodes = [];
  let running = true;
  let timer = null;
  const subscribers = [];
  const historyCache = {};

  let site = { name: 'Loading…', lat: 11.66, lng: 76.63 };
  let rules = {
    tempHigh: 45, smokeHigh: 320, humLow: 25,
    tempWarn: 38, smokeWarn: 180, battLow: 20
  };

  function api(path) {
    return fetch(API_BASE + path, { cache: 'no-store' }).then(r => {
      if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
      return r.json();
    });
  }

  function emit() {
    subscribers.forEach(cb => { try { cb(nodes); } catch (e) { console.error(e); } });
  }

  function refresh() {
    if (!running) { emit(); return Promise.resolve(); }
    return api('/api/nodes')
      .then(next => { nodes = next; emit(); })
      .catch(err => {
        // Gateway unreachable: mark everything offline rather than freezing
        // on stale values that look live.
        console.warn('[live] ' + err.message);
        nodes = nodes.map(n => Object.assign({}, n, { online: false, status: 'offline', fire: false }));
        emit();
      });
  }

  function start() {
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, POLL_MS);
  }

  // ---- public API (mirrors data-source.js exactly) ----
  window.DataSource = {
    label: 'gateway Pi (live)',
    get site() { return site; },
    get rules() { return rules; },

    getNodes() { return Promise.resolve(nodes); },

    getHistory(id) {
      const cached = historyCache[id];
      if (cached && Date.now() - cached.at < HISTORY_TTL_MS) {
        return Promise.resolve(cached.data);
      }
      return api('/api/nodes/' + encodeURIComponent(id) + '/history?days=7')
        .then(data => {
          historyCache[id] = { at: Date.now(), data };
          return data;
        })
        .catch(() => (cached ? cached.data : []));
    },

    subscribe(cb) { subscribers.push(cb); cb(nodes); },

    // On live data the toggle pauses polling — it does not switch off the
    // physical radio. Turning the real gateway off is a job for ssh.
    setGateway(on) {
      running = !!on;
      if (running) refresh();
      else {
        nodes = nodes.map(n => Object.assign({}, n, { online: false, status: 'offline', fire: false }));
        emit();
      }
    },
    isGatewayOn() { return running; },

    // Demo controls do not exist against real hardware. app.js calls these
    // from the Simulate/Clear buttons, so they must be present but inert.
    triggerFire() {
      console.warn('[live] Simulate fire is disabled on real data. ' +
                   'To test the alert path, hold a lit match near a node ' +
                   'or temporarily lower RULES in pi/common/config.py.');
      return null;
    },
    clearFire() {}
  };

  // Pull site config once, then start polling.
  api('/api/site')
    .then(s => {
      site = { name: s.name, lat: s.lat, lng: s.lng };
      rules = s.rules;
      const label = document.getElementById('siteName');
      if (label) label.textContent = s.name;
    })
    .catch(() => console.warn('[live] /api/site unavailable, using defaults'))
    .then(refresh)
    .then(start);
})();
