# Forest Fire Detection Dashboard

Real-time monitoring dashboard for a LoRaWAN forest-fire sensor network. Satellite map with live node markers, per-node sensor readout, 7-day history charts, gateway status, and a fire-alert banner.

Currently runs on **simulated data** so the whole UI works end-to-end. Swapping in real telemetry means editing one file.

## Features

| Whiteboard item | Implemented as |
|---|---|
| Map with node markers | Leaflet + Esri satellite imagery, markers colored by status |
| Click node → readings | Detail panel: temperature, smoke, humidity, battery, fire Y/N |
| Fire Alert — which node, `NO ALERTS` by default | Top banner: green `NO ALERTS` → amber `ELEVATED RISK` → red `FIRE ALERT` naming the node |
| Gateway ON/OFF | Header toggle; OFF pauses telemetry and marks all nodes offline |
| Node 1..N — previous 7 days reading | Bottom panel: node list + 7-day chart, switchable metric |

Extras: 24-hour sparkline per node, RSSI and last-packet age, danger radius drawn around a firing node, `Simulate fire event` button for demos and viva.

## Run locally

No build step. Any static server works:

```bash
python3 -m http.server 8080
# open http://localhost:8080
```

Opening `index.html` directly via `file://` also works.

## Connecting real sensors

The dashboard only ever talks to `window.DataSource`. Keep this contract and change the internals of `data-source.js`:

```js
DataSource.getNodes()       // -> Promise<Node[]>
DataSource.getHistory(id)   // -> Promise<Reading[]>  (last 7 days)
DataSource.subscribe(cb)    // cb(nodes) on every update
DataSource.setGateway(bool)
DataSource.isGatewayOn()
DataSource.rules            // threshold object used for coloring
```

```
Node    = { id, name, lat, lng, online, status, temp, smoke, hum, batt, fire, rssi, lastSeen }
Reading = { t: <epoch ms>, temp, smoke, hum, batt }
```

`status` is one of `normal | warning | fire | offline`.

### Option A — The Things Network (MQTT over WebSocket)

```js
// npm-free: load mqtt.js from CDN in index.html, then:
const client = mqtt.connect('wss://eu1.cloud.thethings.network:8883', {
  username: 'YOUR-APP-ID@ttn',
  password: 'NNSXS.YOUR-API-KEY'
});

client.subscribe('v3/YOUR-APP-ID@ttn/devices/+/up');

client.on('message', (topic, buf) => {
  const msg = JSON.parse(buf.toString());
  const d   = msg.uplink_message.decoded_payload;   // your payload formatter output
  const id  = msg.end_device_ids.device_id;

  updateNode(id, {
    temp: d.temperature,
    hum:  d.humidity,
    smoke: d.smoke,
    batt: d.battery,
    lat: msg.uplink_message.rx_metadata[0].location?.latitude,
    lng: msg.uplink_message.rx_metadata[0].location?.longitude,
    rssi: msg.uplink_message.rx_metadata[0].rssi,
    lastSeen: Date.parse(msg.received_at)
  });
});
```

TTN gives you live uplinks but not 7-day history — pair it with a TTN Storage Integration query or your own database for `getHistory()`.

### Option B — your own HTTP API

```js
getNodes()      -> fetch('/api/nodes').then(r => r.json())
getHistory(id)  -> fetch(`/api/nodes/${id}/history?days=7`).then(r => r.json())
subscribe(cb)   -> setInterval(() => getNodes().then(cb), 5000)
```

## Fire decision rule

Set in `data-source.js` → `RULES`, and should mirror your node firmware:

```
FIRE     temp >= 45 °C  AND  smoke >= 320 ppm  AND  humidity <= 25 %
WARNING  temp >= 38 °C  OR   smoke >= 180 ppm  OR   battery <= 20 %
```

Requiring all three conditions for `FIRE` is what suppresses false positives from a hot afternoon or a passing vehicle. Tune against your own field data before deployment.

## Deploying

A GitHub Actions workflow (`.github/workflows/deploy.yml`) publishes to GitHub Pages on every push to `main`. Enable it once at **Settings → Pages → Source → GitHub Actions**.

## Files

```
index.html        markup
style.css         dark theme
data-source.js    data layer — replace this to go live
app.js            map, charts, alerts, interaction
```

## License

MIT
