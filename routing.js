/* ============================================================
   routing.js — multi-hop routing layer for the sensor network.

   The dashboard's data source owns *what* the nodes measure. This file
   owns *how* those readings reach the gateway. It implements four things
   that a single-hop star topology cannot demonstrate:

     1. Cluster-head election with residual-energy weighting  (LEACH-style)
     2. A backup cluster head that takes over when the primary dies
     3. Per-node routing tables with acknowledged, retried forwarding
     4. Greedy geographic forwarding, and recovery from LOCAL MINIMA
        via perimeter (face) routing on a Gabriel graph   (GPSR-style)

   Plus a first-order radio energy model, so batteries drain according to
   what each node actually transmits. That matters: a cluster head relays
   for its whole cluster, so it dies first, which is precisely what makes
   rotation and the backup head necessary rather than decorative.

   No dependencies, no DOM access. window.Routing is pure logic so it can
   be unit-tested headlessly.
   ============================================================ */

(function () {
  'use strict';

  // ---- tunables ------------------------------------------------------

  const CFG = {
    rangeM: 620,          // radio range, metres. Mean node spacing is ~390 m,
                          // so this gives roughly 6-8 neighbours per node.
    chFraction: 0.12,     // target share of nodes acting as cluster heads
    rotateEveryRounds: 5, // re-elect heads this often; 0 disables rotation
                          // entirely, which is worth running as a comparison —
                          // see the lifetime experiment in ROUTING.md
    chMinSeparationM: 620,// two cluster heads should not sit on top of each other
    ackRetries: 3,        // transmissions before a hop is declared failed
    ackLossRate: 0.06,    // per-hop probability an ACK is missed
    maxHops: 40,          // routing gives up past this — also stops any loop
    heartbeatMissesToPromote: 3,

    // First-order radio model, the one the LEACH papers use.
    eElec: 50e-9,         // J/bit, radio electronics
    eAmp: 100e-12,        // J/bit/m^2, transmit amplifier
    packetBits: 96,       // our 12-byte packet
    batteryJoules: 12000, // usable energy in a node's cells (~3.3 Wh)

    // IMPORTANT caveat, and a difference from the LEACH literature: those
    // papers model motes, where the radio is the dominant consumer and a
    // packet costs microjoules. Ours are Raspberry Pis. A Pi 4 draws around
    // 2 W awake against a few milliwatts of radio, so the SoC dominates and
    // battery life is set almost entirely by how long the node stays awake —
    // which is why duty cycling matters far more here than transmit power.
    // The radio terms above are kept because they still decide the *relative*
    // cost of being a cluster head, which is what drives rotation.
    roundSeconds: 60,     // wall-clock time one protocol round represents
    activePowerW: 2.1,    // Pi 4 + sensors, awake
    sleepPowerW: 0.35,    // Pi low-power idle. A Pi cannot truly sleep like a
                          // mote — this floor is why battery deployments are hard.

    // Duty cycling
    dutyWakeMs: 1200,     // radio awake window per cycle
    dutyPeriodMs: 12000,  // full cycle
  };

  // Degrees -> metres, at the site latitude. Good enough over a few km.
  const M_PER_DEG_LAT = 110574;
  const M_PER_DEG_LNG = 108900;   // cos(11.66 deg) applied

  // ---- state ---------------------------------------------------------

  let gateway = null;        // { id, lat, lng }
  let nodes = [];            // [{ id, lat, lng }]
  let byId = {};
  let obstructions = [];     // [{ lat1, lng1, lat2, lng2 }] — links may not cross
  let adjacency = {};        // id -> [id]        full radio graph
  let planar = {};           // id -> [id]        Gabriel subgraph, for perimeter mode
  let roles = {};            // id -> 'head' | 'backup' | 'member' | 'dead'
  let clusterOf = {};        // id -> cluster-head id
  let tables = {};           // id -> routing table entry
  let energy = {};           // id -> joules remaining
  let missedHeartbeats = {}; // id -> count, for backup promotion
  let dutyPhase = {};        // id -> ms offset into the duty cycle
  let roundNo = 0;
  let events = [];           // recent protocol events, for the dashboard

  const stats = {
    delivered: 0, dropped: 0, retries: 0,
    localMinima: 0, perimeterHops: 0, promotions: 0, rounds: 0,
  };

  // ---- geometry ------------------------------------------------------

  function metres(a, b) {
    const dx = (a.lat - b.lat) * M_PER_DEG_LAT;
    const dy = (a.lng - b.lng) * M_PER_DEG_LNG;
    return Math.hypot(dx, dy);
  }

  // Standard segment-intersection test. Used to decide whether a radio link
  // is blocked by terrain — a ridge line between two nodes.
  function ccw(ax, ay, bx, by, cx, cy) {
    return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax);
  }

  function segmentsCross(p1, p2, p3, p4) {
    return ccw(p1.x, p1.y, p3.x, p3.y, p4.x, p4.y) !== ccw(p2.x, p2.y, p3.x, p3.y, p4.x, p4.y)
        && ccw(p1.x, p1.y, p2.x, p2.y, p3.x, p3.y) !== ccw(p1.x, p1.y, p2.x, p2.y, p4.x, p4.y);
  }

  function xy(p) {
    return { x: p.lng * M_PER_DEG_LNG, y: p.lat * M_PER_DEG_LAT };
  }

  // Intersection point of two segments, or null if they don't cross. Perimeter
  // routing needs the actual point, not just a yes/no, to know whether a face
  // change moves the packet closer to the destination.
  function segIntersectPoint(p1, p2, p3, p4) {
    const d = (p2.x - p1.x) * (p4.y - p3.y) - (p2.y - p1.y) * (p4.x - p3.x);
    if (Math.abs(d) < 1e-9) return null;                 // parallel
    const t = ((p3.x - p1.x) * (p4.y - p3.y) - (p3.y - p1.y) * (p4.x - p3.x)) / d;
    const u = ((p3.x - p1.x) * (p2.y - p1.y) - (p3.y - p1.y) * (p2.x - p1.x)) / d;
    if (t < 0 || t > 1 || u < 0 || u > 1) return null;
    return { x: p1.x + t * (p2.x - p1.x), y: p1.y + t * (p2.y - p1.y) };
  }

  function dist2d(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

  function blocked(a, b) {
    for (const o of obstructions) {
      if (segmentsCross(xy(a), xy(b),
                        xy({ lat: o.lat1, lng: o.lng1 }),
                        xy({ lat: o.lat2, lng: o.lng2 }))) return true;
    }
    return false;
  }

  function alive(id) {
    return roles[id] !== 'dead' && (energy[id] === undefined || energy[id] > 0);
  }

  // ---- topology ------------------------------------------------------

  function all() {
    return gateway ? nodes.concat([gateway]) : nodes.slice();
  }

  function buildAdjacency() {
    adjacency = {};
    const list = all();
    list.forEach(n => { adjacency[n.id] = []; });

    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i], b = list[j];
        if (!alive(a.id) || !alive(b.id)) continue;
        if (metres(a, b) > CFG.rangeM) continue;
        if (blocked(a, b)) continue;          // terrain — this is what creates voids
        adjacency[a.id].push(b.id);
        adjacency[b.id].push(a.id);
      }
    }
    buildPlanar();
  }

  // Gabriel graph: keep edge (u,v) only if no other node sits inside the circle
  // having uv as its diameter. Planarising is what makes face routing terminate
  // — on a graph with crossing edges the right-hand rule can loop forever.
  function buildPlanar() {
    planar = {};
    Object.keys(adjacency).forEach(id => { planar[id] = []; });

    for (const uid of Object.keys(adjacency)) {
      for (const vid of adjacency[uid]) {
        if (uid >= vid) continue;             // consider each edge once
        const u = byId[uid], v = byId[vid];
        const mid = { lat: (u.lat + v.lat) / 2, lng: (u.lng + v.lng) / 2 };
        const r = metres(u, v) / 2;

        let witness = false;
        for (const wid of adjacency[uid]) {
          if (wid === vid) continue;
          if (metres(byId[wid], mid) < r) { witness = true; break; }
        }
        if (!witness) {
          planar[uid].push(vid);
          planar[vid].push(uid);
        }
      }
    }
  }

  // ---- cluster-head election ----------------------------------------

  // LEACH elects heads at random with probability p, but pure randomness puts
  // heads next to each other and picks flat batteries. This weights the draw by
  // residual energy and enforces a minimum separation, which is what the
  // energy-aware LEACH variants do.
  function electHeads() {
    const living = nodes.filter(n => alive(n.id));
    if (!living.length) return;

    const target = Math.max(1, Math.round(living.length * CFG.chFraction));

    const scored = living.map(n => ({
      id: n.id,
      // Residual-energy fraction, nudged so ties don't always break the same way.
      score: (energy[n.id] / CFG.batteryJoules) + pseudoRandom(n.id, roundNo) * 0.15,
    })).sort((a, b) => b.score - a.score);

    const heads = [];
    for (const cand of scored) {
      if (heads.length >= target) break;
      const tooClose = heads.some(h => metres(byId[h], byId[cand.id]) < CFG.chMinSeparationM);
      if (!tooClose) heads.push(cand.id);
    }

    // A head must be able to reach something, or it is a head of nothing.
    const usable = heads.filter(h => (adjacency[h] || []).length > 0);

    nodes.forEach(n => {
      if (!alive(n.id)) { roles[n.id] = 'dead'; return; }
      roles[n.id] = usable.indexOf(n.id) >= 0 ? 'head' : 'member';
    });

    assignClusters(usable);
    electBackups(usable);
  }

  function assignClusters(heads) {
    clusterOf = {};
    if (!heads.length) return;

    nodes.forEach(n => {
      if (!alive(n.id)) return;
      if (roles[n.id] === 'head') { clusterOf[n.id] = n.id; return; }

      // Join the nearest head that is actually within radio range, otherwise
      // the nearest head at all — an out-of-range member will route multi-hop.
      const inRange = heads.filter(h => (adjacency[n.id] || []).indexOf(h) >= 0);
      const pool = inRange.length ? inRange : heads;
      let best = null, bestD = Infinity;
      for (const h of pool) {
        const d = metres(byId[n.id], byId[h]);
        if (d < bestD) { bestD = d; best = h; }
      }
      clusterOf[n.id] = best;
    });
  }

  // The backup is the highest-energy member of the cluster that can hear the
  // head directly — it has to notice the head going quiet.
  function electBackups(heads) {
    for (const h of heads) {
      const members = nodes.filter(n =>
        alive(n.id) && clusterOf[n.id] === h && n.id !== h &&
        (adjacency[h] || []).indexOf(n.id) >= 0);

      if (!members.length) continue;
      members.sort((a, b) => energy[b.id] - energy[a.id]);
      roles[members[0].id] = 'backup';
    }
  }

  // Deterministic pseudo-randomness, so a reload reproduces the same network.
  function pseudoRandom(id, salt) {
    let h = 2166136261;
    const s = String(id) + ':' + salt;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 100000) / 100000;
  }

  // ---- greedy + perimeter routing (GPSR) -----------------------------

  /**
   * Route from `srcId` to the gateway.
   *
   * Greedy phase: hop to whichever neighbour is closest to the destination.
   * If no neighbour is closer than the current node, that node is a LOCAL
   * MINIMUM — the packet is stuck in a void even though a path may exist
   * around it. We then switch to perimeter mode and walk the face of the
   * planar graph by the right-hand rule, returning to greedy as soon as we
   * reach a node closer to the destination than the point where we got stuck.
   */
  function route(srcId) {
    const dest = gateway;
    let path = [srcId];
    const modes = [];
    let cur = srcId;
    let hops = 0;
    let hitLocalMinimum = false;

    while (hops < CFG.maxHops) {
      if (cur === dest.id) {
        return { path, modes, hops, delivered: true, localMinimum: hitLocalMinimum };
      }

      const dHere = metres(byId[cur], dest);
      const nbrs = (adjacency[cur] || []).filter(alive);

      if (!nbrs.length) {
        return { path, modes, hops, delivered: false, localMinimum: hitLocalMinimum,
                 reason: 'isolated' };
      }

      // ---- greedy phase: hop to whichever neighbour is closest to the sink --
      let best = null, bestD = dHere;
      for (const nb of nbrs) {
        const d = metres(byId[nb], dest);
        if (d < bestD) { bestD = d; best = nb; }
      }

      if (best !== null) {
        cur = best;
        path.push(cur); modes.push('greedy'); hops++;
        continue;
      }

      // ---- LOCAL MINIMUM --------------------------------------------------
      // Every neighbour is farther from the gateway than we are. A route may
      // still exist, but only by moving *away* first — which greedy will never
      // do. Recover by walking the faces of the planar graph incident to this
      // node, in counterclockwise order starting from the direction of the
      // gateway, until one of them yields a node closer than we are now.
      hitLocalMinimum = true;
      stats.localMinima++;
      note(cur, 'local-minimum',
           'no neighbour closer to gateway than ' + Math.round(dHere) + ' m');

      const candidates = firstEdgeCandidates(cur, dest);
      let escaped = false;

      for (const firstNb of candidates) {
        const walk = walkFace(cur, firstNb, dest, dHere, CFG.maxHops - hops);
        stats.perimeterHops += walk.hops;

        if (walk.escapedAt) {
          path = path.concat(walk.path);
          walk.path.forEach(() => modes.push('perimeter'));
          hops += walk.path.length;
          cur = walk.escapedAt;
          escaped = true;
          note(cur, 'perimeter-exit',
               'escaped the void after ' + walk.hops + ' perimeter hops');
          break;
        }
        note(cur, 'face-exhausted',
             'face via ' + firstNb + ' closed with no progress — trying next face');
      }

      if (!escaped) {
        return { path, modes, hops, delivered: false, localMinimum: true,
                 reason: 'partitioned — every incident face traversed, no route exists' };
      }
    }

    return { path, modes, hops, delivered: false, localMinimum: hitLocalMinimum,
             reason: 'hop limit' };
  }

  /**
   * Walk one face of the planar graph by the right-hand rule.
   *
   * Returns as soon as it finds a node strictly closer to `dest` than
   * `stuckDist` — that is the escape back to greedy. If the walk returns to
   * the starting node and re-takes the same first edge, the face has been
   * fully traversed with no progress and this face is a dead end.
   */
  function walkFace(startId, firstNb, dest, stuckDist, hopBudget) {
    const path = [firstNb];
    const firstEdge = startId + '>' + firstNb;
    let prev = startId, cur = firstNb, hops = 1;

    if (cur === dest.id) return { path, escapedAt: cur, hops };
    if (metres(byId[cur], dest) < stuckDist) return { path, escapedAt: cur, hops };

    while (hops < hopBudget) {
      const pnbrs = (planar[cur] || []).filter(alive);
      if (!pnbrs.length) return { path, escapedAt: null, hops, reason: 'dead end' };

      const next = rightHandNext(cur, prev, pnbrs);

      // Back to where we started, on the same edge: the face is closed.
      if (cur + '>' + next === firstEdge) {
        return { path, escapedAt: null, hops, reason: 'face complete' };
      }

      prev = cur; cur = next;
      path.push(cur); hops++;

      if (cur === dest.id) return { path, escapedAt: cur, hops };
      if (metres(byId[cur], dest) < stuckDist) return { path, escapedAt: cur, hops };
    }

    return { path, escapedAt: null, hops, reason: 'hop budget' };
  }

  // Planar neighbours of the stuck node, ordered counterclockwise starting from
  // the direction of the destination. The first is the textbook GPSR choice;
  // the rest are the other faces incident to this node, tried in turn.
  function firstEdgeCandidates(id, dest) {
    const here = byId[id];
    const refAngle = Math.atan2(
      (dest.lat - here.lat) * M_PER_DEG_LAT,
      (dest.lng - here.lng) * M_PER_DEG_LNG);

    return (planar[id] || []).filter(alive).map(nb => {
      const a = Math.atan2(
        (byId[nb].lat - here.lat) * M_PER_DEG_LAT,
        (byId[nb].lng - here.lng) * M_PER_DEG_LNG);
      let delta = a - refAngle;
      while (delta <= 0) delta += 2 * Math.PI;
      while (delta > 2 * Math.PI) delta -= 2 * Math.PI;
      return { nb, delta };
    }).sort((x, y) => x.delta - y.delta).map(x => x.nb);
  }

  // The right-hand rule: on arriving at `curId` from `prevId`, take the next
  // edge counterclockwise about `curId` from the edge we came in on.
  function rightHandNext(curId, prevId, pnbrs) {
    const here = byId[curId];
    const from = byId[prevId];
    const refAngle = Math.atan2(
      (from.lat - here.lat) * M_PER_DEG_LAT,
      (from.lng - here.lng) * M_PER_DEG_LNG);

    let best = null, bestDelta = Infinity;
    for (const nb of pnbrs) {
      if (nb === prevId && pnbrs.length > 1) continue;   // don't bounce straight back
      const a = Math.atan2(
        (byId[nb].lat - here.lat) * M_PER_DEG_LAT,
        (byId[nb].lng - here.lng) * M_PER_DEG_LNG);
      let delta = a - refAngle;
      while (delta <= 0) delta += 2 * Math.PI;           // strictly counterclockwise
      while (delta > 2 * Math.PI) delta -= 2 * Math.PI;
      if (delta < bestDelta) { bestDelta = delta; best = nb; }
    }
    // Degree-1 node: the only way out is back the way we came.
    return best === null ? prevId : best;
  }

  // ---- routing tables ------------------------------------------------

  // Each node stores where it sends traffic and what that costs. Members send
  // to their cluster head; heads route onward to the gateway.
  function buildTables() {
    tables = {};

    nodes.forEach(n => {
      if (!alive(n.id)) {
        tables[n.id] = { role: 'dead', nextHop: null, hops: null, path: [] };
        return;
      }

      const role = roles[n.id];
      const head = clusterOf[n.id];

      if (role === 'head') {
        const r = route(n.id);
        tables[n.id] = {
          role,
          clusterHead: n.id,
          nextHop: r.path.length > 1 ? r.path[1] : null,
          hops: r.hops,
          path: r.path,
          modes: r.modes,
          delivered: r.delivered,
          localMinimum: r.localMinimum,
          reason: r.reason || null,
          neighbours: (adjacency[n.id] || []).slice(),
        };
      } else {
        const viaHead = head && alive(head);
        const upstream = viaHead ? tables[head] : null;
        tables[n.id] = {
          role,
          clusterHead: head || null,
          nextHop: viaHead ? head : null,
          hops: viaHead && upstream ? upstream.hops + 1 : null,
          path: viaHead && upstream ? [n.id].concat(upstream.path) : [n.id],
          modes: viaHead && upstream ? ['cluster'].concat(upstream.modes || []) : [],
          delivered: viaHead && upstream ? upstream.delivered : false,
          localMinimum: viaHead && upstream ? upstream.localMinimum : false,
          reason: viaHead ? null : 'no cluster head',
          neighbours: (adjacency[n.id] || []).slice(),
        };
      }
    });

    // Heads are built first above only if they happen to come first in the
    // array, so do a second pass for members whose head was built after them.
    nodes.forEach(n => {
      if (!alive(n.id) || roles[n.id] === 'head') return;
      const head = clusterOf[n.id];
      const upstream = head && tables[head];
      if (upstream && upstream.role === 'head') {
        tables[n.id].hops = upstream.hops + 1;
        tables[n.id].path = [n.id].concat(upstream.path);
        tables[n.id].modes = ['cluster'].concat(upstream.modes || []);
        tables[n.id].delivered = upstream.delivered;
        tables[n.id].localMinimum = upstream.localMinimum;
      }
    });
  }

  // ---- transmission, ACK and energy ----------------------------------

  // First-order radio model: transmitting costs electronics plus an amplifier
  // term that grows with the square of distance; receiving costs electronics.
  function txCost(distanceM) {
    return CFG.eElec * CFG.packetBits + CFG.eAmp * CFG.packetBits * distanceM * distanceM;
  }
  function rxCost() {
    return CFG.eElec * CFG.packetBits;
  }

  function drain(id, joules) {
    if (energy[id] === undefined) return;
    energy[id] = Math.max(0, energy[id] - joules);
    if (energy[id] === 0 && roles[id] !== 'dead') {
      roles[id] = 'dead';
      note(id, 'node-dead', 'battery exhausted');
    }
  }

  /**
   * Send one reading from `srcId` along its table entry, paying energy for
   * every hop and requiring an acknowledgement at each. A hop that is not
   * acknowledged is retransmitted up to CFG.ackRetries times.
   */
  function transmit(srcId) {
    const t = tables[srcId];
    if (!t || !t.path || t.path.length < 2) { stats.dropped++; return { ok: false, hops: 0 }; }

    let acked = 0;
    for (let i = 0; i < t.path.length - 1; i++) {
      const a = byId[t.path[i]], b = byId[t.path[i + 1]];
      if (!a || !b) break;
      const d = metres(a, b);

      let ok = false;
      for (let attempt = 0; attempt < CFG.ackRetries; attempt++) {
        drain(a.id, txCost(d));
        if (b.id !== gateway.id) drain(b.id, rxCost());

        // The ACK travels back over the same link and can itself be lost.
        if (pseudoRandom(a.id + '>' + b.id, roundNo * 97 + attempt) > CFG.ackLossRate) {
          ok = true;
          break;
        }
        stats.retries++;
      }

      if (!ok) {
        stats.dropped++;
        note(a.id, 'ack-timeout', 'no acknowledgement from ' + b.id +
             ' after ' + CFG.ackRetries + ' attempts');
        return { ok: false, hops: acked };
      }
      acked++;
    }

    stats.delivered++;
    return { ok: true, hops: acked };
  }

  // ---- backup cluster head promotion ---------------------------------

  // A backup watches its head. Three missed heartbeats and it takes over —
  // this is what stops a dead head taking its whole cluster off the map.
  function checkHeartbeats() {
    nodes.forEach(n => {
      if (roles[n.id] !== 'backup') return;
      const head = clusterOf[n.id];
      if (!head) return;

      if (!alive(head)) {
        missedHeartbeats[n.id] = (missedHeartbeats[n.id] || 0) + 1;

        if (missedHeartbeats[n.id] >= CFG.heartbeatMissesToPromote) {
          roles[n.id] = 'head';
          stats.promotions++;
          missedHeartbeats[n.id] = 0;
          note(n.id, 'backup-promoted', 'took over from failed head ' + head);

          // Everyone who was pointing at the dead head now points here.
          nodes.forEach(m => { if (clusterOf[m.id] === head) clusterOf[m.id] = n.id; });
          clusterOf[n.id] = n.id;
          electBackups([n.id]);
        }
      } else {
        missedHeartbeats[n.id] = 0;
      }
    });
  }

  // ---- duty cycling ---------------------------------------------------

  // Radios are the dominant power draw, and idle listening costs more in total
  // than transmitting because there is so much more of it. Each node keeps its
  // radio off except for a short wake window; heads stay awake longer because
  // they have to be listening when their members report.
  function dutyStateOf(id, nowMs) {
    if (!alive(id)) return 'off';
    const phase = (nowMs + (dutyPhase[id] || 0)) % CFG.dutyPeriodMs;
    const window = roles[id] === 'head' ? CFG.dutyWakeMs * 3 : CFG.dutyWakeMs;
    return phase < window ? 'awake' : 'asleep';
  }

  function dutyCycleRatio(id) {
    const window = roles[id] === 'head' ? CFG.dutyWakeMs * 3 : CFG.dutyWakeMs;
    return window / CFG.dutyPeriodMs;
  }

  // ---- events ---------------------------------------------------------

  function note(nodeId, kind, detail) {
    events.unshift({ t: Date.now(), nodeId, kind, detail, round: roundNo });
    if (events.length > 60) events.pop();
  }

  // ---- public API ------------------------------------------------------

  window.Routing = {
    config: CFG,

    /**
     * @param opts.gateway      { id, lat, lng }
     * @param opts.nodes        [{ id, lat, lng }]
     * @param opts.obstructions [{ lat1, lng1, lat2, lng2 }] terrain blocking links
     */
    configure(opts) {
      gateway = opts.gateway;
      nodes = opts.nodes.slice();
      obstructions = opts.obstructions || [];

      byId = {};
      all().forEach(n => { byId[n.id] = n; });

      roles = {}; clusterOf = {}; energy = {};
      missedHeartbeats = {}; dutyPhase = {}; events = [];
      roundNo = 0;

      nodes.forEach((n, i) => {
        roles[n.id] = 'member';
        // Spread the starting charge a little so election has something to
        // work with on round one.
        energy[n.id] = CFG.batteryJoules * (0.82 + pseudoRandom(n.id, 'init') * 0.18);
        dutyPhase[n.id] = (i * 997) % CFG.dutyPeriodMs;
      });

      buildAdjacency();
      electHeads();
      buildTables();
      return this;
    },

    /** Advance one protocol round: re-elect, rebuild, and send one reading
     *  from every living node. */
    round() {
      roundNo++;
      stats.rounds++;

      checkHeartbeats();
      buildAdjacency();

      // Rotating spreads the cluster-head cost so no single node is drained on
      // everyone else's behalf. Set rotateEveryRounds to 0 to hold the first
      // election forever — the network then dies markedly sooner, which is the
      // clearest demonstration of why rotation exists.
      if (CFG.rotateEveryRounds > 0 && roundNo % CFG.rotateEveryRounds === 0) {
        electHeads();
      }

      buildTables();

      nodes.forEach(n => { if (alive(n.id)) transmit(n.id); });

      // Baseline platform draw — the term that actually empties the battery.
      // A cluster head keeps its radio up three times as long to catch its
      // members' uplinks, so it pays proportionally more and dies sooner.
      // That is precisely what makes rotation and a backup head necessary.
      nodes.forEach(n => {
        if (!alive(n.id)) return;
        const duty = dutyCycleRatio(n.id);
        const watts = duty * CFG.activePowerW + (1 - duty) * CFG.sleepPowerW;
        drain(n.id, watts * CFG.roundSeconds);
      });

      buildTables();
      return this.snapshot();
    },

    /** Kill a node outright — the demo control for cluster-head failover. */
    kill(id) {
      if (!byId[id]) return false;
      energy[id] = 0;
      roles[id] = 'dead';
      note(id, 'node-killed', 'manually taken offline');
      buildAdjacency(); buildTables();
      return true;
    },

    revive(id) {
      if (!byId[id]) return false;
      energy[id] = CFG.batteryJoules * 0.9;
      roles[id] = 'member';
      note(id, 'node-revived', 'brought back online');
      buildAdjacency(); electHeads(); buildTables();
      return true;
    },

    route,
    tableFor(id) { return tables[id] || null; },
    tables() { return tables; },
    roleOf(id) { return roles[id] || 'member'; },
    clusterHeadOf(id) { return clusterOf[id] || null; },
    neighboursOf(id) { return (adjacency[id] || []).slice(); },
    planarNeighboursOf(id) { return (planar[id] || []).slice(); },
    energyOf(id) { return energy[id]; },
    batteryPercentOf(id) { return Math.max(0, 100 * energy[id] / CFG.batteryJoules); },
    dutyStateOf,
    dutyCycleRatio,
    events() { return events.slice(); },
    stats() { return Object.assign({ round: roundNo }, stats); },

    snapshot() {
      return {
        round: roundNo,
        stats: Object.assign({}, stats),
        heads: nodes.filter(n => roles[n.id] === 'head').map(n => n.id),
        backups: nodes.filter(n => roles[n.id] === 'backup').map(n => n.id),
        dead: nodes.filter(n => roles[n.id] === 'dead').map(n => n.id),
        stuck: nodes.filter(n => tables[n.id] && tables[n.id].localMinimum).map(n => n.id),
        unreachable: nodes.filter(n => tables[n.id] && !tables[n.id].delivered && alive(n.id))
                          .map(n => n.id),
      };
    },
  };
})();
