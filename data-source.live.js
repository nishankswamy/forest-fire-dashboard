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

  // Topology and gateway position, from /api/site. Until that lands the
  // dashboard simply draws no overlays rather than guessing.
  let topology = {};
  let tdma = {};
  let gateway = null;
  let command = 'none';
  let commandInfo = {};

  function api(path) {
    return fetch(API_BASE + path, { cache: 'no-store' }).then(r => {
      if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
      return r.json();
    });
  }

  function emit() {
    subscribers.forEach(cb => { try { cb(nodes); } catch (e) { console.error(e); } });
  }

  /**
   * Fill in the routing fields app.js draws with.
   *
   * The gateway already sends role, cluster, via, hops, routePath and
   * dutyRatio — those are REAL, straight off the packets. The rest are
   * derived here so the same dashboard code renders live hardware and the
   * simulator without branching.
   */
  function decorate(node) {
    const path = node.routePath || [];

    // Per-hop colouring. On hardware there is no greedy or perimeter
    // forwarding — every hop is an explicit routing-table decision. The first
    // hop is a member reaching its cluster head; everything after that is a
    // head forwarding toward the sink, which is what 'greedy' shades blue.
    const modes = path.slice(0, -1).map((_, i) => (i === 0 ? 'cluster' : 'greedy'));

    return Object.assign({}, node, {
      clusterHead: topology.headOfCluster
        ? topology.headOfCluster[node.cluster] || null : null,
      routeModes: modes,
      routeOk: node.online && node.hops !== null && node.hops !== undefined,

      // Structurally impossible on this firmware: routing is explicit
      // next-hop, never greedy geographic, so there is no minimum to reach.
      localMinimum: false,

      // The radio is slot-scheduled rather than freely awake. dutyRatio comes
      // from the gateway and is exact.
      duty: node.online ? 'slotted' : 'off'
    });
  }

  function sendCommand(cmd) {
    return fetch(API_BASE + '/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    }).then(r => r.json()).then(res => {
      if (res.ok) command = cmd;
      pollCommand();
      return res;
    }).catch(err => {
      console.warn('[live] command failed: ' + err.message);
      return { ok: false, error: err.message };
    });
  }

  function pollCommand() {
    return api('/api/command')
      .then(c => { command = c.command || 'none'; commandInfo = c; })
      .catch(() => {});
  }

  function refresh() {
    pollCommand();
    if (!running) { emit(); return Promise.resolve(); }
    return api('/api/nodes')
      .then(next => { nodes = next.map(decorate); emit(); })
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

    // app.js checks these before drawing the topology overlays. Without them
    // it hides the routing panel entirely — which is what used to happen on
    // live data, losing the multi-hop view exactly when it became real.
    get hasRouting() { return !!topology.roles; },
    get gateway() { return gateway; },

    // No terrain obstruction is modelled on hardware; the real radio either
    // reaches or it does not, and linktest.py is how you find out.
    obstructions: [],

    get topology() { return topology; },

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
    clearFire() {},

    /* ---- system control -------------------------------------------
       Same three names as the simulator, but these travel over the air:
       api.py records the command, gateway.py stamps it into the next
       beacon, and every node acts on it. One frame to reach cluster A,
       two to reach cluster B. */
    isRunning() { return running && command !== 'halt'; },

    stop() { return sendCommand('halt'); },
    resume() { return sendCommand('resume'); },
    restart() { return sendCommand('restart'); },

    get command() { return command; },
    get commandInfo() { return commandInfo; },

    /* ---- routing, mirroring data-source.js so app.js needs no branch ---- */
    routing: {
      snapshot() {
        if (!topology.roles) return null;
        const ids = Object.keys(topology.roles);
        return {
          round: null,
          heads: ids.filter(id => topology.roles[id] === 'head'),
          backups: Object.values(topology.backupHead || {}),
          dead: nodes.filter(n => !n.online).map(n => n.id),
          stuck: [],          // cannot occur: routing is explicit, not greedy
          unreachable: nodes.filter(n => n.online && !n.routeOk).map(n => n.id)
        };
      },
      stats() { return null; },      // no per-round counters from hardware
      events() { return []; },
      tableFor(id) { return nodes.find(n => n.id === id) || null; },
      neighboursOf() { return []; }, // the gateway cannot see who hears whom
      config() { return tdma; },

      // Killing a node means walking to it. Say so rather than failing quietly.
      kill(id) {
        console.warn('[live] Cannot kill ' + id + ' from the dashboard. ' +
                     'ssh in and: sudo systemctl stop fire-node');
        return false;
      },
      revive(id) {
        console.warn('[live] Cannot revive ' + id + ' from the dashboard. ' +
                     'ssh in and: sudo systemctl start fire-node');
        return false;
      }
    }
  };

  // Pull site config once, then start polling.
  api('/api/site')
    .then(s => {
      site = { name: s.name, lat: s.lat, lng: s.lng };
      rules = s.rules;
      topology = s.topology || {};
      tdma = s.tdma || {};
      gateway = s.gateway || null;
      const label = document.getElementById('siteName');
      if (label) label.textContent = s.name;
    })
    .catch(() => console.warn('[live] /api/site unavailable, using defaults'))
    .then(refresh)
    .then(start);
})();
