"""
db.py — SQLite storage for received readings.

One table, one index, WAL mode so the receiver can write while the API reads.
SQLite is the right call here: a 7-day window of 5 nodes at 1 uplink/min is
~50k rows, which it handles without noticing, and there is no server to babysit
on a Pi that may lose power without warning.
"""

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL,
    ts        REAL    NOT NULL,          -- unix seconds, gateway clock
    temp      REAL    NOT NULL,
    hum       REAL    NOT NULL,
    smoke     REAL    NOT NULL,
    batt      REAL    NOT NULL,
    fire      INTEGER NOT NULL DEFAULT 0,
    rssi      INTEGER,
    seq       INTEGER,
    flags     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_readings_node_ts ON readings (node_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);
"""


def connect(path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert(conn, reading, rssi=None, ts=None):
    """Store one decoded packet. `reading` is protocol.decode() output."""
    flags = 0
    if reading.get('sensor_error'):
        flags |= 0x02
    if reading.get('simulated'):
        flags |= 0x04

    conn.execute(
        'INSERT INTO readings (node_id, ts, temp, hum, smoke, batt, fire, rssi, seq, flags)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (reading['node_id'], ts if ts is not None else time.time(),
         reading['temp'], reading['hum'], reading['smoke'], reading['batt'],
         1 if reading['fire'] else 0, rssi, reading.get('seq'), flags))
    conn.commit()


def latest_per_node(conn):
    """Most recent reading for every node that has ever reported."""
    rows = conn.execute("""
        SELECT r.* FROM readings r
        JOIN (SELECT node_id, MAX(ts) AS ts FROM readings GROUP BY node_id) m
          ON r.node_id = m.node_id AND r.ts = m.ts
        GROUP BY r.node_id
        ORDER BY r.node_id
    """).fetchall()
    return [dict(row) for row in rows]


def history(conn, node_id, days=7, max_points=400):
    """Readings for one node over the window, thinned to at most max_points.

    Thinning matters: a week at 1/min is 10,080 rows per node, and pushing
    that to a browser chart is wasteful when the canvas is 800 px wide.
    """
    since = time.time() - days * 86400
    total = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE node_id=? AND ts>=?',
        (node_id, since)).fetchone()[0]

    if total == 0:
        return []

    # Ceiling division: floor division would round the stride down and let the
    # result exceed max_points (3360 rows / 400 -> stride 8 -> 420 points).
    stride = max(1, -(-total // max_points))
    rows = conn.execute("""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (ORDER BY ts) AS rn
            FROM readings WHERE node_id=? AND ts>=?
        ) WHERE rn %% %d = 0 ORDER BY ts
    """ % stride, (node_id, since)).fetchall()

    return [dict(row) for row in rows]


def prune(conn, retention_days=7):
    """Delete readings older than the retention window. Called hourly by the
    gateway so the SD card doesn't fill up over a season."""
    cutoff = time.time() - retention_days * 86400
    cur = conn.execute('DELETE FROM readings WHERE ts < ?', (cutoff,))
    conn.commit()
    return cur.rowcount
