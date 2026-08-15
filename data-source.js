/* ============================================================
   data-source.js  —  THE ONLY FILE YOU REPLACE FOR REAL DATA
   ------------------------------------------------------------
   The dashboard talks to exactly one object: window.DataSource.
   It must expose:

     DataSource.getNodes()      -> Promise<Node[]>
     DataSource.getHistory(id)  -> Promise<Reading[]>   (last 7 days)
     DataSource.subscribe(cb)   -> calls cb(nodes) on every update

   Node    = { id, name, lat, lng, online, temp, smoke, hum, batt,
               fire, rssi, lastSeen }
   Reading = { t: <epoch ms>, temp, smoke, hum, batt }

   To go live: keep the shape, change the innards. See README.md
   for a ready-made TTN (The Things Network) MQTT adapter.
   ============================================================ */

(function () {
  'use strict';

  // ---- site config -------------------------------------------------
  const SITE = { name: 'Bandipur Sector 4', lat: 11.6600, lng: 76.6300 };

  // Fire-decision thresholds (mirror these in your node firmware)
  const RULES = {
    tempHigh:  45,   // °C
    smokeHigh: 320,  // ppm  (MQ-2 analog -> ppm estimate)
    humLow:    25,   // %
    tempWarn:  38,
    smokeWarn: 180,
    battLow:   20
  };

  // ---- node roster ---------------------------------------------------
  // The hardware is SIX Raspberry Pis: one gateway (LoRa addr 0, no marker
  // of its own) and five sensor nodes, N-01..N-05. Those five entries below
  // mirror pi/common/config.py exactly — same ids, names and coordinates —
  // so the simulated map and the live map agree on the real nodes.
  //
  // Everything past N-05 exists only in simulation, to show the dashboard
  // holding up at deployment scale. Set SIM_NODES to 5 to see exactly what
  // the live gateway will serve.
  const SIM_NODES = 50;

  const REAL_NODE_DEFS = [
    { id: 'N-01', name: 'Node 1 — Ridge East',   dLat:  0.0042, dLng: -0.0058 },
    { id: 'N-02', name: 'Node 2 — Fire Line A',  dLat:  0.0036, dLng:  0.0011 },
    { id: 'N-03', name: 'Node 3 — Watchtower',   dLat:  0.0021, dLng:  0.0062 },
    { id: 'N-04', name: 'Node 4 — Creek Bed',    dLat: -0.0009, dLng: -0.0071 },
    { id: 'N-05', name: 'Node 5 — Bamboo Belt',  dLat: -0.0004, dLng: -0.0016 }
  ];

  const TERRAIN = [
    'Dry Slope', 'Trail Head', 'Teak Grove', 'Boundary', 'Saddle',
    'Gully', 'Plateau', 'Scrub Flat', 'Rock Face', 'Elephant Corridor',
    'Sal Stand', 'Waterhole', 'Ravine', 'Escarpment', 'Meadow'
  ];
  const SECTOR = ['East', 'West', 'North', 'South', 'A', 'B', 'C', 'Upper', 'Lower', 'Far'];

  // Golden-angle (sunflower) placement: even coverage with no clustering,
  // and fully deterministic, so the map looks identical on every reload.
  const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
  const SPREAD = 0.0145;                  // ~1.6 km from the site centre

  function generatedDef(i) {              // i is 0-based, past the real five
    const num = REAL_NODE_DEFS.length + i + 1;
    const k = i + 1;
    // The +4 offset holds the innermost ring away from the site centre so the
    // generated nodes don't crowd the five real ones sitting near the middle.
    const count = Math.max(1, SIM_NODES - REAL_NODE_DEFS.length);
    const radius = SPREAD * Math.sqrt((k + 4) / (count + 4));
    const angle = k * GOLDEN_ANGLE;
    return {
      id: 'N-' + String(num).padStart(2, '0'),
      name: 'Node ' + num + ' — ' + TERRAIN[i % TERRAIN.length] + ' ' +
            SECTOR[Math.floor(i / TERRAIN.length) % SECTOR.length],
      dLat: radius * Math.cos(angle),
      dLng: radius * Math.sin(angle) * 1.018   // 1° lng is shorter than 1° lat at 11.7 N
    };
  }

  const NODE_DEFS = REAL_NODE_DEFS.slice(0, SIM_NODES).concat(
    Array.from({ length: Math.max(0, SIM_NODES - REAL_NODE_DEFS.length) },
               (_, i) => generatedDef(i))
  );

  // Nodes that start offline. A dead node on the map is worth being able
  // to point at during a demo.
  const OFFLINE_AT_START = new Set(['N-09', 'N-23', 'N-41']);

  // The gateway Pi. It has no sensors, so it is not in NODE_DEFS — but the
  // routing layer needs its position, because every route terminates here.
  const GATEWAY = { id: 'GW', lat: SITE.lat, lng: SITE.lng };

  // A ridge running across the middle of the site. LoRa links may not cross
  // it, which carves a void in the topology — and a void is what produces the
  // local-minima problem that greedy geographic forwarding cannot solve on its
  // own. Without an obstruction the node field is uniform, every route is
  // greedy, and there is nothing to demonstrate.
  const OBSTRUCTIONS = [
    { lat1: 11.6540, lng1: 76.6360, lat2: 11.6660, lng2: 76.6360, name: 'Ridge line' }
  ];

  const HOUR = 3600e3;
  const HISTORY_HOURS = 7 * 24;
  const STEP_MIN = 30;                       // one reading every 30 min
  const POINTS = HISTORY_HOURS * (60 / STEP_MIN);

  // deterministic pseudo-random so charts look the same on reload
  function rng(seed) {
    let s = seed >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  // ---- generate 7 days of history per node -------------------------
  const history = {};
  const state = {};
  let fireNodeId = null;

  function buildHistory(def, idx) {
    const r = rng(idx * 7919 + 13);
    const now = Date.now();
    const out = [];
    const baseTemp = 23.5 + r() * 3;
    const baseHum  = 54 + r() * 12;
    // Cycle the starting charge instead of ramping it with the index —
    // with 50 nodes a straight ramp would draw a visible battery gradient
    // across the map. Every 13th node starts low so the amber low-battery
    // warning is actually represented on screen.
    let batt = (idx % 13 === 5) ? 24 - (idx % 4) : 96 - (idx % 10) * 1.4;

    for (let i = POINTS - 1; i >= 0; i--) {
      const t = now - i * STEP_MIN * 60e3;
      const d = new Date(t);
      const hourFrac = d.getHours() + d.getMinutes() / 60;
      // diurnal cycle: peak ~15:00, trough ~05:00
      const diurnal = Math.sin(((hourFrac - 9) / 24) * 2 * Math.PI);
      // slow multi-day heat wave building toward the present
      const trend = (1 - i / POINTS) * 2.2;

      const temp = baseTemp + diurnal * 5.5 + trend + (r() - 0.5) * 1.6;
      const hum  = clamp(baseHum - diurnal * 16 - trend * 1.8 + (r() - 0.5) * 5, 8, 96);
      const smoke = 42 + Math.max(0, diurnal) * 14 + (r() - 0.5) * 18 + trend * 3;

      batt -= 0.0055 + r() * 0.004;

      out.push({
        t,
        temp:  round1(temp),
        hum:   round1(hum),
        smoke: Math.max(10, Math.round(smoke)),
        batt:  round1(clamp(batt, 3, 100))
      });
    }
    return out;
  }

  NODE_DEFS.forEach((def, i) => {
    history[def.id] = buildHistory(def, i);
    const last = history[def.id][history[def.id].length - 1];
    state[def.id] = {
      id: def.id,
      name: def.name,
      lat: SITE.lat + def.dLat,
      lng: SITE.lng + def.dLng,
      online: !OFFLINE_AT_START.has(def.id),
      rssi: -78 - Math.round(rng(i + 3)() * 30),
      lastSeen: last.t,
      temp: last.temp, smoke: last.smoke, hum: last.hum, batt: last.batt,
      fire: false
    };
  });

  // ---- routing layer -------------------------------------------------
  // routing.js owns how readings reach the gateway: clustering, routing
  // tables, acknowledged forwarding, and local-minima recovery. It is
  // optional — if the script isn't loaded the dashboard still works, it just
  // shows no topology.
  const hasRouting = typeof window.Routing !== 'undefined';

  if (hasRouting) {
    window.Routing.configure({
      gateway: GATEWAY,
      obstructions: OBSTRUCTIONS,
      nodes: NODE_DEFS.map(d => ({
        id: d.id,
        lat: SITE.lat + d.dLat,
        lng: SITE.lng + d.dLng
      }))
    });

    // Nodes that start offline are dead as far as the topology is concerned,
    // so routes are computed around them from the first tick.
    OFFLINE_AT_START.forEach(id => window.Routing.kill(id));
  }

  // ---- fire decision -----------------------------------------------
  function evaluate(n) {
    if (!n.online) return 'offline';
    if (n.temp >= RULES.tempHigh && n.smoke >= RULES.smokeHigh && n.hum <= RULES.humLow) return 'fire';
    if (n.temp >= RULES.tempWarn || n.smoke >= RULES.smokeWarn || n.batt <= RULES.battLow) return 'warning';
    return 'normal';
  }

  // ---- live tick ----------------------------------------------------
  const subscribers = [];
  let gatewayOn = true;
  let tick = 0;

  function step() {
    if (!gatewayOn) { emit(); return; }
    tick++;

    // Advance the protocol one round: heartbeats, re-election, table rebuild,
    // then one acknowledged uplink per living node.
    if (hasRouting) {
      window.Routing.round();
      // A node whose battery the energy model has emptied goes offline here,
      // which is what triggers backup-head promotion on the next round.
      NODE_DEFS.forEach(def => {
        if (window.Routing.roleOf(def.id) === 'dead') state[def.id].online = false;
      });
    }

    NODE_DEFS.forEach((def, i) => {
      const n = state[def.id];
      if (!n.online) return;

      const jitter = (Math.random() - 0.5);

      if (fireNodeId === n.id) {
        // fire ramp: heat + smoke climb fast, humidity collapses
        n.temp  = round1(Math.min(78, n.temp + 1.9 + Math.random()));
        n.smoke = Math.min(950, Math.round(n.smoke + 46 + Math.random() * 30));
        n.hum   = round1(Math.max(6, n.hum - 2.4));
      } else if (fireNodeId && neighbour(fireNodeId, n.id)) {
        // adjacent nodes drift up more slowly
        n.temp  = round1(Math.min(52, n.temp + 0.5 + Math.random() * 0.4));
        n.smoke = Math.min(420, Math.round(n.smoke + 11 + Math.random() * 9));
        n.hum   = round1(Math.max(14, n.hum - 0.7));
      } else {
        n.temp  = round1(clamp(n.temp + jitter * 0.5, 18, 44));
        n.smoke = Math.max(12, Math.round(n.smoke + jitter * 9));
        n.hum   = round1(clamp(n.hum + jitter * 1.2, 10, 95));
      }

      // With routing active the energy model owns battery level — it accounts
      // for duty cycle and cluster-head overhead, so a head visibly drains
      // faster than its members. Without it, fall back to a flat trickle.
      n.batt = hasRouting
        ? round1(window.Routing.batteryPercentOf(n.id))
        : round1(Math.max(2, n.batt - 0.004));

      n.rssi = Math.max(-120, Math.min(-52, n.rssi + Math.round(jitter * 4)));
      n.lastSeen = Date.now();
      n.fire = evaluate(n) === 'fire';

      // append to history every 4th tick so the 7-day chart keeps moving
      if (tick % 4 === 0) {
        const h = history[n.id];
        h.push({ t: Date.now(), temp: n.temp, smoke: n.smoke, hum: n.hum, batt: n.batt });
        if (h.length > POINTS) h.shift();
      }
    });

    emit();
  }

  function neighbour(aId, bId) {
    const a = state[aId], b = state[bId];
    if (!a || !b) return false;
    const dx = (a.lat - b.lat) * 111, dy = (a.lng - b.lng) * 109;
    // At 50 nodes the mean spacing is ~390 m, so 750 m picks up roughly the
    // first ring around the fire — a visible front rather than half the map.
    return Math.hypot(dx, dy) < 0.75;
  }

  function emit() {
    const snapshot = snapshotNodes();
    subscribers.forEach(cb => { try { cb(snapshot); } catch (e) { console.error(e); } });
  }

  function snapshotNodes() {
    const now = Date.now();
    return NODE_DEFS.map(d => {
      const n = state[d.id];
      const online = gatewayOn && n.online;
      const base = Object.assign({}, n, {
        online,
        status: online ? evaluate(n) : 'offline',
        fire: online && evaluate(n) === 'fire'
      });

      if (!hasRouting) return base;

      const R = window.Routing;
      const t = R.tableFor(d.id) || {};
      // Read battery straight from the energy model rather than the cached
      // state, so a snapshot taken between ticks is still accurate.
      return Object.assign(base, {
        batt: round1(R.batteryPercentOf(d.id)),
        role: R.roleOf(d.id),               // head | backup | member | dead
        clusterHead: t.clusterHead || null,
        nextHop: t.nextHop || null,
        hops: t.hops,
        routePath: t.path || [],
        routeModes: t.modes || [],
        routeOk: !!t.delivered,
        localMinimum: !!t.localMinimum,
        routeReason: t.reason || null,
        neighbours: t.neighbours || [],
        duty: gatewayOn ? R.dutyStateOf(d.id, now) : 'off',
        dutyRatio: R.dutyCycleRatio(d.id)
      });
    });
  }

  // ---- helpers ------------------------------------------------------
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function round1(v) { return Math.round(v * 10) / 10; }

  // ---- public API ---------------------------------------------------
  window.DataSource = {
    label: 'simulated',
    site: SITE,
    rules: RULES,

    getNodes()        { return Promise.resolve(snapshotNodes()); },
    getHistory(id)    { return Promise.resolve((history[id] || []).slice()); },
    subscribe(cb)     { subscribers.push(cb); cb(snapshotNodes()); },

    setGateway(on)    { gatewayOn = !!on; emit(); },
    isGatewayOn()     { return gatewayOn; },

    /* demo controls — delete these two when you go live */
    triggerFire(id) {
      fireNodeId = id || NODE_DEFS[Math.floor(Math.random() * (NODE_DEFS.length - 1))].id;
      const n = state[fireNodeId];
      n.temp = 46.2; n.smoke = 340; n.hum = 21;
      n.fire = true;
      emit();
      return fireNodeId;
    },
    clearFire() {
      if (fireNodeId) {
        const n = state[fireNodeId];
        n.temp = 31.4; n.smoke = 70; n.hum = 48; n.fire = false;
        NODE_DEFS.forEach(d => {
          const m = state[d.id];
          if (m.temp > 40) { m.temp = 33; m.smoke = 80; m.hum = 45; m.fire = false; }
        });
      }
      fireNodeId = null;
      emit();
    },

    /* ---- routing / topology ----------------------------------------
       Everything the dashboard needs to draw the network and explain what
       the protocol is doing. Null-safe when routing.js isn't loaded. */
    gateway: GATEWAY,
    obstructions: OBSTRUCTIONS,
    hasRouting,

    routing: {
      snapshot()      { return hasRouting ? window.Routing.snapshot() : null; },
      stats()         { return hasRouting ? window.Routing.stats() : null; },
      events()        { return hasRouting ? window.Routing.events() : []; },
      tableFor(id)    { return hasRouting ? window.Routing.tableFor(id) : null; },
      neighboursOf(id){ return hasRouting ? window.Routing.neighboursOf(id) : []; },
      config()        { return hasRouting ? window.Routing.config : null; },

      /* Demo control: take a node out and watch the network respond. Killing
         a cluster head is the one worth showing — its backup is promoted and
         the cluster re-homes within a few rounds. */
      kill(id) {
        if (!hasRouting) return false;
        window.Routing.kill(id);
        if (state[id]) state[id].online = false;
        emit();
        return true;
      },
      revive(id) {
        if (!hasRouting) return false;
        window.Routing.revive(id);
        if (state[id]) state[id].online = true;
        emit();
        return true;
      }
    }
  };

  setInterval(step, 3000);
})();
