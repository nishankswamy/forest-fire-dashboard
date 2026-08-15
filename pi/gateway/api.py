#!/usr/bin/env python3
"""
api.py — HTTP API + dashboard host, runs on the GATEWAY Pi.

    pip3 install flask flask-cors
    python3 api.py

Then from any device on the same network:
    http://<gateway-pi-ip>:5000

Endpoints
    GET /api/site                    site name, map centre, thresholds
    GET /api/nodes                   latest reading per node
    GET /api/nodes/<id>/history?days=7
    GET /api/health                  uptime + row count, for monitoring
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))

from flask import Flask, jsonify, request, send_from_directory

import config
import db

# The dashboard lives two levels up from pi/gateway/
DASHBOARD_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..'))

app = Flask(__name__, static_folder=None)
STARTED = time.time()

try:
    from flask_cors import CORS
    CORS(app)
except ImportError:
    # Only needed if you serve the dashboard from somewhere other than this Pi.
    pass


def get_db():
    if not hasattr(app, '_db'):
        app._db = db.connect(config.DB_PATH)
    return app._db


def node_meta(node_id):
    meta = config.NODES.get(node_id)
    if meta:
        return meta
    # An unconfigured node still shows up, stacked at the site centre, so you
    # notice it exists and can add real coordinates to config.py.
    return {'name': 'Node %d (unconfigured)' % node_id,
            'lat': SITE_CENTRE[0], 'lng': SITE_CENTRE[1]}


SITE_CENTRE = (
    sum(n['lat'] for n in config.NODES.values()) / max(1, len(config.NODES)),
    sum(n['lng'] for n in config.NODES.values()) / max(1, len(config.NODES)),
)


# ------------------------------------------------------------------ api ----

@app.route('/api/site')
def site():
    return jsonify({
        'name': config.SITE_NAME,
        'lat': SITE_CENTRE[0],
        'lng': SITE_CENTRE[1],
        'rules': {
            'tempHigh': config.RULES['temp_high'],
            'smokeHigh': config.RULES['smoke_high'],
            'humLow': config.RULES['hum_low'],
            'tempWarn': config.RULES['temp_warn'],
            'smokeWarn': config.RULES['smoke_warn'],
            'battLow': config.RULES['batt_low'],
        },
        'offlineAfter': config.OFFLINE_AFTER,
        'gateway': {
            'id': 'GW',
            'lat': config.GATEWAY_POS['lat'],
            'lng': config.GATEWAY_POS['lng'],
        },
        # The dashboard draws the real topology from this rather than assuming
        # a star, so what you see on screen matches what the radios do.
        'topology': {
            'roles': {('N-%02d' % n): config.role_of(n) for n in config.NODES},
            'clusterOf': {('N-%02d' % n): c for n, c in config.CLUSTER_OF.items()},
            'headOfCluster': {c: ('N-%02d' % n) for c, n in config.HEAD_OF_CLUSTER.items()},
            'backupHead': {c: ('N-%02d' % n) for c, n in config.BACKUP_HEAD.items()},
            'routes': {('N-%02d' % n): [
                'GW' if h == config.GATEWAY_ADDR else 'N-%02d' % h
                for h in config.routes_for(n)] for n in config.ROUTES},
        },
        'tdma': {
            'frameSeconds': config.FRAME_SECONDS,
            'slotMs': config.SLOT_MS,
            'slotCount': config.SLOT_COUNT,
            'dataSlot': {('N-%02d' % n): s for n, s in config.DATA_SLOT.items()},
            'forwardSlot': {('N-%02d' % n): s for n, s in config.FORWARD_SLOT.items()},
            'dutyCycle': {('N-%02d' % n): round(config.duty_cycle_of(n), 4)
                          for n in config.NODES},
        },
    })


@app.route('/api/nodes')
def nodes():
    rows = db.latest_per_node(get_db())
    now = time.time()
    seen = {}

    for row in rows:
        node_id = row['node_id']
        meta = node_meta(node_id)
        online = (now - row['ts']) <= config.OFFLINE_AFTER
        status = config.evaluate(row['temp'], row['smoke'], row['hum'], row['batt']) \
            if online else 'offline'

        seen[node_id] = {
            'id': 'N-%02d' % node_id,
            'name': meta['name'],
            'lat': meta['lat'],
            'lng': meta['lng'],
            'online': online,
            'status': status,
            'temp': round(row['temp'], 1),
            'hum': round(row['hum'], 1),
            'smoke': round(row['smoke']),
            'batt': round(row['batt'], 1),
            'fire': bool(row['fire']) and online,
            'rssi': row['rssi'],
            'lastSeen': row['ts'] * 1000.0,
            'sensorError': bool(row['flags'] & 0x02),
            'simulated': bool(row['flags'] & 0x04),
            # How this reading actually got here, straight from the packet.
            'role': config.role_of(node_id),
            'cluster': config.CLUSTER_OF.get(node_id),
            'via': ('GW' if row['via'] == config.GATEWAY_ADDR
                    else 'N-%02d' % row['via']) if row['via'] is not None else None,
            'hops': row['hops'] if row['hops'] is not None else 1,
            'nextHop': _hop_label(config.routes_for(node_id)[0]),
            'routePath': _route_path(node_id),
            'dutyRatio': round(config.duty_cycle_of(node_id), 4),
        }

    # Configured nodes that have never reported still belong on the map,
    # shown offline — a node that never came up is exactly what you want to see.
    for node_id, meta in config.NODES.items():
        if node_id not in seen:
            seen[node_id] = {
                'id': 'N-%02d' % node_id, 'name': meta['name'],
                'lat': meta['lat'], 'lng': meta['lng'],
                'online': False, 'status': 'offline',
                'temp': 0, 'hum': 0, 'smoke': 0, 'batt': 0,
                'fire': False, 'rssi': None, 'lastSeen': 0,
                'sensorError': False, 'simulated': False,
                'role': config.role_of(node_id),
                'cluster': config.CLUSTER_OF.get(node_id),
                'via': None, 'hops': None,
                'nextHop': _hop_label(config.routes_for(node_id)[0]),
                'routePath': _route_path(node_id),
                'dutyRatio': round(config.duty_cycle_of(node_id), 4),
            }

    return jsonify([seen[k] for k in sorted(seen)])


def _hop_label(addr):
    return 'GW' if addr == config.GATEWAY_ADDR else 'N-%02d' % addr


def _route_path(node_id, _depth=0):
    """Expected path to the gateway by following primary next hops.

    Guarded against a cycle introduced by a bad edit to ROUTES — it returns
    what it has rather than recursing forever.
    """
    path = [_hop_label(node_id)]
    hop = config.routes_for(node_id)[0]
    seen = {node_id}
    while hop != config.GATEWAY_ADDR and hop not in seen and len(path) < 8:
        seen.add(hop)
        path.append(_hop_label(hop))
        hop = config.routes_for(hop)[0]
    path.append('GW')
    return path


@app.route('/api/routes')
def routes():
    """Observed routing over the last hour — what the radios actually did,
    as opposed to what config.py intends."""
    return jsonify([{
        'id': 'N-%02d' % r['node_id'],
        'via': _hop_label(r['via']) if r['via'] is not None else None,
        'hops': r['hops'],
        'packets': r['n'],
        'lastSeen': r['last_ts'] * 1000.0,
    } for r in db.route_summary(get_db())])


@app.route('/api/nodes/<node_id>/history')
def history(node_id):
    numeric = int(str(node_id).replace('N-', '').lstrip('0') or 0)
    days = min(30, max(1, request.args.get('days', 7, type=int)))
    rows = db.history(get_db(), numeric, days=days)

    return jsonify([{
        't': row['ts'] * 1000.0,
        'temp': round(row['temp'], 1),
        'hum': round(row['hum'], 1),
        'smoke': round(row['smoke']),
        'batt': round(row['batt'], 1),
    } for row in rows])


@app.route('/api/health')
def health():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    recent = conn.execute('SELECT COUNT(*) FROM readings WHERE ts > ?',
                          (time.time() - 3600,)).fetchone()[0]
    return jsonify({
        'ok': True,
        'uptimeSeconds': round(time.time() - STARTED),
        'totalReadings': total,
        'lastHour': recent,
        'db': config.DB_PATH,
    })


# ------------------------------------------------------------ dashboard ----

@app.route('/')
def index():
    return send_from_directory(DASHBOARD_DIR, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(DASHBOARD_DIR, filename)


if __name__ == '__main__':
    print('[api] serving dashboard from %s' % DASHBOARD_DIR)
    print('[api] http://0.0.0.0:5000')
    app.run(host='0.0.0.0', port=5000, threaded=True)
