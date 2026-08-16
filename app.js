/* app.js — dashboard rendering & interaction */
(function () {
  'use strict';

  const DS = window.DataSource;
  const $ = id => document.getElementById(id);

  let nodes = [];
  let selectedId = null;
  let map, markers = {}, ring = null;
  let historyChart = null, sparkChart = null;
  let metric = 'temp';

  // Routing overlay layers, kept separate so each can be toggled independently.
  let topoLayer = null, routeLayer = null, ridgeLayer = null, gatewayMarker = null;
  const show = { topology: true, route: true, ridge: true };

  const ROUTE_COLORS = {
    cluster:   '#8b949e',   // member -> its cluster head
    greedy:    '#58a6ff',   // greedy geographic forwarding
    perimeter: '#d29922'    // perimeter mode, recovering from a local minimum
  };

  const COLORS = { normal: '#2ea043', warning: '#d29922', fire: '#f85149', offline: '#6e7681' };
  const METRICS = {
    temp:  { label: 'Temperature (°C)', unit: '°C', color: '#f0883e' },
    smoke: { label: 'Smoke (ppm)',      unit: ' ppm', color: '#a371f7' },
    hum:   { label: 'Humidity (%)',     unit: '%',  color: '#58a6ff' },
    batt:  { label: 'Battery (%)',      unit: '%',  color: '#3fb950' }
  };

  /* ---------------- map ---------------- */
  function initMap() {
    map = L.map('map', { zoomControl: true, attributionControl: true })
           .setView([DS.site.lat, DS.site.lng], 14);

    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics' }
    ).addTo(map);

    L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, opacity: 0.9, attribution: '&copy; CARTO' }
    ).addTo(map);
  }

  function markerIcon(node) {
    const cls = node.status === 'fire' ? 'nm-fire'
              : node.status === 'warning' ? 'nm-warn'
              : node.status === 'offline' ? 'nm-off' : 'nm-ok';
    const sel = node.id === selectedId ? ' nm-sel' : '';
    // Role ring: heads and backups are drawn larger with a coloured border so
    // the cluster structure reads at a glance without clicking anything.
    const role = node.role === 'head' ? ' nm-head'
               : node.role === 'backup' ? ' nm-backup' : '';
    const stuck = node.localMinimum ? ' nm-min' : '';
    const n = node.id.replace('N-0', '').replace('N-', '');
    return L.divIcon({
      className: '',
      html: `<div class="node-marker ${cls}${sel}${role}${stuck}">${n}</div>`,
      iconSize: [18, 18],
      iconAnchor: [9, 9]
    });
  }

  /* ---------------- routing overlay ---------------- */

  // The ridge that blocks LoRa links. Drawing it matters: without it on screen
  // the local-minima behaviour looks arbitrary rather than caused by terrain.
  function renderRidge() {
    if (!map) return;
    if (ridgeLayer) { map.removeLayer(ridgeLayer); ridgeLayer = null; }
    if (!show.ridge || !DS.obstructions) return;

    ridgeLayer = L.layerGroup(DS.obstructions.map(o =>
      L.polyline([[o.lat1, o.lng1], [o.lat2, o.lng2]], {
        color: '#f85149', weight: 4, opacity: 0.55, dashArray: '2 8'
      }).bindTooltip(o.name || 'Terrain obstruction — links cannot cross')
    )).addTo(map);
  }

  // Called at boot AND on every data tick. On live data the gateway position
  // arrives with /api/site, which resolves after boot — draw it once at boot
  // and the marker never appears at all.
  function renderGateway() {
    if (!map || !DS.gateway) return;
    if (!gatewayMarker) {
      gatewayMarker = L.marker([DS.gateway.lat, DS.gateway.lng], {
        icon: L.divIcon({
          className: '',
          html: '<div class="node-marker nm-gateway" title="Gateway Pi">GW</div>',
          iconSize: [26, 26], iconAnchor: [13, 13]
        }),
        zIndexOffset: 1000
      }).addTo(map).bindPopup('<strong>Gateway Pi</strong><br>LoRa addr 0 — every route ends here');
    }
  }

  // Cluster membership: a faint line from each node to its cluster head.
  function renderTopology() {
    if (topoLayer) { map.removeLayer(topoLayer); topoLayer = null; }
    if (!show.topology || !DS.hasRouting) return;

    const byId = {};
    nodes.forEach(n => byId[n.id] = n);
    const lines = [];

    nodes.forEach(n => {
      if (!n.online || !n.clusterHead || n.clusterHead === n.id) return;
      const head = byId[n.clusterHead];
      if (!head) return;
      lines.push(L.polyline([[n.lat, n.lng], [head.lat, head.lng]], {
        color: ROUTE_COLORS.cluster, weight: 1, opacity: 0.35
      }));
    });

    topoLayer = L.layerGroup(lines).addTo(map);
  }

  // The multi-hop path from the selected node to the gateway, coloured by the
  // mode used on each hop. Amber segments are perimeter mode — the packet
  // routing around a void it could not cross greedily.
  function renderRoute() {
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    if (!show.route || !DS.hasRouting) return;

    const n = nodes.find(x => x.id === selectedId);
    if (!n || !n.routePath || n.routePath.length < 2) return;

    const pos = {};
    nodes.forEach(x => pos[x.id] = [x.lat, x.lng]);
    if (DS.gateway) pos[DS.gateway.id] = [DS.gateway.lat, DS.gateway.lng];

    const segs = [];
    for (let i = 0; i < n.routePath.length - 1; i++) {
      const a = pos[n.routePath[i]], b = pos[n.routePath[i + 1]];
      if (!a || !b) continue;
      const mode = (n.routeModes && n.routeModes[i]) || 'greedy';
      segs.push(L.polyline([a, b], {
        color: ROUTE_COLORS[mode] || ROUTE_COLORS.greedy,
        weight: mode === 'perimeter' ? 3.5 : 2.5,
        opacity: 0.95,
        dashArray: mode === 'cluster' ? '4 4' : null
      }).bindTooltip(mode + ' hop: ' + n.routePath[i] + ' → ' + n.routePath[i + 1]));
    }

    // Mark where greedy forwarding failed.
    if (n.localMinimum) {
      const stuckIdx = (n.routeModes || []).indexOf('perimeter');
      const stuckId = stuckIdx > 0 ? n.routePath[stuckIdx] : n.routePath[0];
      if (pos[stuckId]) {
        segs.push(L.circleMarker(pos[stuckId], {
          radius: 11, color: ROUTE_COLORS.perimeter, weight: 2,
          fillColor: ROUTE_COLORS.perimeter, fillOpacity: 0.18
        }).bindTooltip('Local minimum — no neighbour closer to the gateway'));
      }
    }

    routeLayer = L.layerGroup(segs).addTo(map);
  }

  function renderMarkers() {
    nodes.forEach(n => {
      if (!markers[n.id]) {
        markers[n.id] = L.marker([n.lat, n.lng], { icon: markerIcon(n) })
          .addTo(map)
          .on('click', () => select(n.id));
      } else {
        markers[n.id].setIcon(markerIcon(n));
      }
      markers[n.id].bindPopup(
        `<strong>${n.name}</strong><br>${fmt(n.temp)}°C &middot; ${n.smoke} ppm &middot; ${fmt(n.hum)}%` +
        `<br><span style="color:${COLORS[n.status]}">${n.status.toUpperCase()}</span>`
      );
    });

    // danger ring around a firing node
    const fireNode = nodes.find(n => n.status === 'fire');
    if (ring) { map.removeLayer(ring); ring = null; }
    if (fireNode) {
      ring = L.circle([fireNode.lat, fireNode.lng], {
        radius: 420, color: '#f85149', weight: 1.5,
        fillColor: '#f85149', fillOpacity: 0.16
      }).addTo(map);
    }
  }

  /* ---------------- alert bar ---------------- */
  function renderAlert() {
    const bar = $('alertBar');
    const fires = nodes.filter(n => n.status === 'fire');
    const warns = nodes.filter(n => n.status === 'warning');
    bar.className = 'alert-bar';

    if (fires.length) {
      bar.classList.add('alert-fire');
      $('alertIcon').textContent = '!';
      $('alertTitle').textContent = 'FIRE ALERT';
      $('alertDetail').textContent =
        `${fires.map(n => n.name).join(', ')} — ${fmt(fires[0].temp)}°C, ` +
        `${fires[0].smoke} ppm smoke, ${fmt(fires[0].hum)}% humidity. Dispatch immediately.`;
      $('clearFire').hidden = false;
      $('simulateFire').hidden = true;
    } else if (warns.length) {
      bar.classList.add('alert-warn');
      $('alertIcon').textContent = '!';
      $('alertTitle').textContent = 'ELEVATED RISK';
      $('alertDetail').textContent =
        `${warns.length} node${warns.length > 1 ? 's' : ''} above warning threshold: ` +
        warns.map(n => n.name.split('—')[0].trim()).join(', ') + '.';
      $('clearFire').hidden = true;
      $('simulateFire').hidden = false;
    } else {
      bar.classList.add('alert-none');
      $('alertIcon').textContent = '✓';
      $('alertTitle').textContent = 'NO ALERTS';
      $('alertDetail').textContent = DS.isGatewayOn()
        ? 'All nodes reporting normal conditions.'
        : 'Gateway is OFF — no telemetry is being received.';
      $('clearFire').hidden = true;
      $('simulateFire').hidden = false;
    }
  }

  /* ---------------- detail panel ---------------- */
  function renderDetail() {
    const n = nodes.find(x => x.id === selectedId);
    if (!n) { $('detailEmpty').hidden = false; $('detailBody').hidden = true; return; }
    $('detailEmpty').hidden = true;
    $('detailBody').hidden = false;

    $('detailTitle').textContent = n.name;
    const chip = $('detailStatus');
    chip.textContent = n.status.toUpperCase();
    chip.className = 'chip ' + ({ normal: 'chip-ok', warning: 'chip-warn', fire: 'chip-fire' }[n.status] || '');

    setVal('dTemp',  fmt(n.temp) + ' °C',  n.temp >= DS.rules.tempHigh ? 'v-fire' : n.temp >= DS.rules.tempWarn ? 'v-warn' : '');
    setVal('dSmoke', n.smoke + ' ppm',     n.smoke >= DS.rules.smokeHigh ? 'v-fire' : n.smoke >= DS.rules.smokeWarn ? 'v-warn' : '');
    setVal('dHum',   fmt(n.hum) + ' %',    n.hum <= DS.rules.humLow ? 'v-warn' : '');
    setVal('dBatt',  fmt(n.batt) + ' %',   n.batt <= DS.rules.battLow ? 'v-warn' : '');
    setVal('dFire',  n.fire ? 'YES' : 'NO', n.fire ? 'v-fire' : 'v-ok');

    $('dId').textContent    = n.id;
    $('dCoord').textContent = n.lat.toFixed(4) + ', ' + n.lng.toFixed(4);
    $('dRssi').textContent  = n.online ? n.rssi + ' dBm' : '—';
    $('dSeen').textContent  = n.online ? ago(n.lastSeen) : 'offline';

    renderRoutingBox(n);
    DS.getHistory(n.id).then(h => drawSpark(h.slice(-48)));
  }

  const ROLE_LABEL = {
    head: 'Cluster head', backup: 'Backup head',
    member: 'Member', dead: 'Dead'
  };

  function renderRoutingBox(n) {
    const box = $('routingBox');
    if (!DS.hasRouting) { box.hidden = true; return; }
    box.hidden = false;

    const role = $('dRole');
    role.textContent = ROLE_LABEL[n.role] || n.role || '—';
    role.className = 'role-' + (n.role || 'member');

    $('dHead').textContent = n.clusterHead
      ? (n.clusterHead === n.id ? 'self' : n.clusterHead) : '—';
    $('dNext').textContent = n.nextHop || (n.role === 'dead' ? '—' : 'gateway');
    $('dHops').textContent = (n.hops === null || n.hops === undefined) ? '—' : n.hops;
    $('dNbrs').textContent = n.neighbours && n.neighbours.length
      ? n.neighbours.length + ' in range' : '—';
    $('dDuty').textContent = n.duty === 'off' ? '—'
      : n.duty + ' · ' + Math.round((n.dutyRatio || 0) * 100) + '% cycle';

    $('dPath').textContent = (n.routePath && n.routePath.length > 1)
      ? n.routePath.join(' → ') : 'no route';

    const warn = $('dRouteWarn');
    if (!n.routeOk && n.role !== 'dead') {
      warn.hidden = false;
      warn.textContent = 'Unreachable — ' + (n.routeReason || 'no path to gateway');
    } else if (n.localMinimum) {
      warn.hidden = false;
      warn.textContent = 'Hit a local minimum; recovered via perimeter routing.';
    } else {
      warn.hidden = true;
    }

    const kill = $('killNode');
    kill.textContent = n.role === 'dead' ? 'Revive node' : 'Kill node';
    kill.dataset.node = n.id;
    kill.dataset.action = n.role === 'dead' ? 'revive' : 'kill';
  }

  /* ---------------- network protocol panel ---------------- */
  function renderNetwork() {
    if (!DS.hasRouting) return;
    const snap = DS.routing.snapshot();
    if (!snap) return;

    // Live hardware reports topology but no per-round counters — the gateway
    // sees delivered packets, not the retries and elections that happened out
    // in the field. Render what exists rather than blanking the whole panel.
    const st = DS.routing.stats();

    $('netRound').textContent = st ? 'round ' + st.round : 'live';
    $('nHeads').textContent   = snap.heads.length;
    $('nBackups').textContent = snap.backups.length;

    if (st) {
      const total = st.delivered + st.dropped;
      $('nPdr').textContent     = total ? (100 * st.delivered / total).toFixed(1) + '%' : '—';
      $('nRetries').textContent = st.retries;
      $('nMinima').textContent  = st.localMinima;
      $('nPromo').textContent   = st.promotions;
    } else {
      const online = nodes.filter(n => n.online).length;
      $('nPdr').textContent     = online + '/' + nodes.length;
      $('nRetries').textContent = '—';
      $('nMinima').textContent  = '0';
      $('nPromo').textContent   = '—';
    }

    const ul = $('eventList');
    const scroll = ul.scrollTop;
    ul.innerHTML = '';
    DS.routing.events().slice(0, 14).forEach(e => {
      const li = document.createElement('li');
      li.className = 'ev ev-' + e.kind;
      li.innerHTML = `<span class="ev-k">${e.kind.replace(/-/g, ' ')}</span>` +
                     `<span class="ev-n">${e.nodeId}</span>` +
                     `<span class="ev-d">${e.detail}</span>`;
      ul.appendChild(li);
    });
    ul.scrollTop = scroll;
  }

  function setVal(id, text, cls) {
    const el = $(id);
    el.textContent = text;
    el.className = 'ro-v ' + (cls || '');
  }

  /* ---------------- charts ---------------- */
  const gridOpts = {
    grid: { color: '#26313d' },
    ticks: { color: '#8b98a5', font: { size: 10 } },
    border: { color: '#26313d' }
  };

  function drawSpark(readings) {
    const ctx = $('sparkChart');
    const labels = readings.map(r => new Date(r.t).getHours() + ':00');
    const data = {
      labels,
      datasets: [
        { label: '°C', data: readings.map(r => r.temp), borderColor: '#f0883e',
          backgroundColor: '#f0883e22', tension: .35, pointRadius: 0, borderWidth: 1.6, yAxisID: 'y', fill: true },
        { label: 'ppm', data: readings.map(r => r.smoke), borderColor: '#a371f7',
          tension: .35, pointRadius: 0, borderWidth: 1.6, yAxisID: 'y1' }
      ]
    };
    const opts = {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#8b98a5', boxWidth: 10, font: { size: 10 } } } },
      scales: {
        x: Object.assign({}, gridOpts, { ticks: { color: '#8b98a5', font: { size: 9 }, maxTicksLimit: 6 } }),
        y:  Object.assign({}, gridOpts, { position: 'left' }),
        y1: Object.assign({}, gridOpts, { position: 'right', grid: { display: false } })
      }
    };
    if (sparkChart) { sparkChart.data = data; sparkChart.update('none'); }
    else sparkChart = new Chart(ctx, { type: 'line', data, options: opts });
  }

  function drawHistory() {
    const ids = selectedId ? [selectedId] : nodes.slice(0, 4).map(n => n.id);
    Promise.all(ids.map(id => DS.getHistory(id))).then(sets => {
      const base = sets[0] || [];
      const labels = base.map(r => dayLabel(r.t));
      const palette = ['#f0883e', '#58a6ff', '#a371f7', '#3fb950'];
      const datasets = sets.map((s, i) => ({
        label: (nodes.find(n => n.id === ids[i]) || {}).name || ids[i],
        data: s.map(r => r[metric]),
        borderColor: selectedId ? METRICS[metric].color : palette[i % 4],
        backgroundColor: (selectedId ? METRICS[metric].color : palette[i % 4]) + '1f',
        fill: !!selectedId, tension: .3, pointRadius: 0, borderWidth: 1.6
      }));

      const data = { labels, datasets };
      const opts = {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: '#8b98a5', boxWidth: 10, font: { size: 11 } } },
          tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.formattedValue}${METRICS[metric].unit}` } }
        },
        scales: {
          x: Object.assign({}, gridOpts, { ticks: { color: '#8b98a5', font: { size: 10 }, maxTicksLimit: 8 } }),
          y: Object.assign({}, gridOpts, {
            title: { display: true, text: METRICS[metric].label, color: '#8b98a5', font: { size: 11 } }
          })
        }
      };
      if (historyChart) { historyChart.data = data; historyChart.options = opts; historyChart.update('none'); }
      else historyChart = new Chart($('historyChart'), { type: 'line', data, options: opts });
    });
  }

  /* ---------------- node list ---------------- */
  function renderList() {
    const ul = $('nodeList');
    // The list is rebuilt on every tick. With a handful of nodes that was
    // invisible; at 50 the list scrolls, and a naive rebuild would throw the
    // user back to the top every few seconds. Preserve the scroll offset.
    const scroll = ul.scrollTop;
    ul.innerHTML = '';
    nodes.forEach(n => {
      const li = document.createElement('li');
      if (n.id === selectedId) li.classList.add('active');
      li.innerHTML =
        `<i class="lg" style="background:${COLORS[n.status]}"></i>` +
        `<span class="nl-name">${n.name.split('—')[0].trim()}</span>` +
        `<span class="nl-val">${n.online ? fmt(n[metric]) + METRICS[metric].unit : '—'}</span>`;
      li.title = n.name;
      li.onclick = () => select(n.id);
      ul.appendChild(li);
    });
    ul.scrollTop = scroll;
  }

  /* ---------------- interaction ---------------- */
  function select(id) {
    selectedId = (selectedId === id) ? null : id;
    renderMarkers(); renderDetail(); renderList(); drawHistory();
    // The route overlay is per-selection, so it has to be redrawn here too —
    // onData() alone only refreshes it on the next telemetry tick.
    renderRoute();
    if (selectedId && markers[selectedId]) markers[selectedId].openPopup();
  }

  function onData(next) {
    nodes = next;
    renderGateway(); renderRidge();
    renderMarkers(); renderAlert(); renderDetail(); renderList();
    renderTopology(); renderRoute(); renderNetwork();
    $('lastSync').textContent = DS.isGatewayOn()
      ? 'last sync ' + new Date().toLocaleTimeString()
      : 'telemetry paused';
  }

  /* ---------------- helpers ---------------- */
  function fmt(v) { return (typeof v === 'number') ? v.toFixed(1) : '—'; }
  function ago(t) {
    const s = Math.round((Date.now() - t) / 1000);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    return Math.round(s / 3600) + 'h ago';
  }
  function dayLabel(t) {
    const d = new Date(t);
    return d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' +
           String(d.getHours()).padStart(2, '0') + ':00';
  }

  /* ---------------- boot ---------------- */
  document.addEventListener('DOMContentLoaded', function () {
    $('siteName').textContent = DS.site.name;
    $('srcLabel').textContent = DS.label;

    initMap();
    renderGateway();
    renderRidge();
    DS.subscribe(onData);
    drawHistory();

    // ---- routing overlay toggles ----
    [['showTopology', 'topology', renderTopology],
     ['showRoute',    'route',    renderRoute],
     ['showRidge',    'ridge',    renderRidge]].forEach(([elId, key, fn]) => {
      const el = $(elId);
      if (!el) return;
      el.addEventListener('change', function () { show[key] = this.checked; fn(); });
    });

    // ---- kill / revive, the cluster-head failover demo ----
    const kill = $('killNode');
    if (kill) {
      // kill()/revive() emit synchronously, which re-renders this panel and
      // flips the button's action mid-handler. Read the role from the current
      // snapshot rather than the button, and guard against re-entry, so a
      // double-click can't kill and immediately revive the same node.
      let toggling = false;
      kill.addEventListener('click', function () {
        if (toggling) return;
        const id = this.dataset.node;
        if (!id) return;
        const n = nodes.find(x => x.id === id);
        toggling = true;
        try {
          if (n && n.role === 'dead') DS.routing.revive(id);
          else DS.routing.kill(id);
        } finally {
          toggling = false;
        }
      });
    }

    $('gatewayToggle').addEventListener('click', function () {
      const on = !DS.isGatewayOn();
      DS.setGateway(on);
      this.classList.toggle('pill-on', on);
      this.classList.toggle('pill-off', !on);
      this.setAttribute('aria-pressed', String(on));
      $('gatewayText').textContent = on ? 'ON' : 'OFF';
    });

    $('metricSelect').addEventListener('change', function () {
      metric = this.value;
      drawHistory(); renderList();
    });

    $('simulateFire').addEventListener('click', function () {
      const id = DS.triggerFire();
      selectedId = id;
      renderDetail(); renderList(); drawHistory();
    });

    $('clearFire').addEventListener('click', function () { DS.clearFire(); });

    setInterval(drawHistory, 12000);
  });
})();
