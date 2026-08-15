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

  const NODE_DEFS = [
    { id: 'N-01', name: 'Node 1 — Ridge East',   dLat:  0.0042, dLng: -0.0058 },
    { id: 'N-02', name: 'Node 2 — Fire Line A',  dLat:  0.0036, dLng:  0.0011 },
    { id: 'N-03', name: 'Node 3 — Watchtower',   dLat:  0.0021, dLng:  0.0062 },
    { id: 'N-04', name: 'Node 4 — Creek Bed',    dLat: -0.0009, dLng: -0.0071 },
    { id: 'N-05', name: 'Node 5 — Bamboo Belt',  dLat: -0.0004, dLng: -0.0016 },
    { id: 'N-06', name: 'Node 6 — Dry Slope',    dLat: -0.0018, dLng:  0.0038 },
    { id: 'N-07', name: 'Node 7 — Trail Head',   dLat: -0.0046, dLng: -0.0040 },
    { id: 'N-08', name: 'Node 8 — Teak Grove',   dLat: -0.0052, dLng:  0.0024 },
    { id: 'N-09', name: 'Node 9 — Boundary S',   dLat: -0.0068, dLng:  0.0066 }
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
    let batt = 96 - idx * 1.4;

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
      online: def.id !== 'N-09',            // one node starts offline, on purpose
      rssi: -78 - Math.round(rng(i + 3)() * 30),
      lastSeen: last.t,
      temp: last.temp, smoke: last.smoke, hum: last.hum, batt: last.batt,
      fire: false
    };
  });

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

      n.batt = round1(Math.max(2, n.batt - 0.004));
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
    return Math.hypot(dx, dy) < 0.85;   // within ~850 m
  }

  function emit() {
    const snapshot = snapshotNodes();
    subscribers.forEach(cb => { try { cb(snapshot); } catch (e) { console.error(e); } });
  }

  function snapshotNodes() {
    return NODE_DEFS.map(d => {
      const n = state[d.id];
      const online = gatewayOn && n.online;
      return Object.assign({}, n, {
        online,
        status: online ? evaluate(n) : 'offline',
        fire: online && evaluate(n) === 'fire'
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
    }
  };

  setInterval(step, 3000);
})();
