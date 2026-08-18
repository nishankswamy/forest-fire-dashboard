#!/usr/bin/env python3
"""
fieldreport.py — turn live gateway data into the report's measurement table.

Answers RQ6 ("do the guarantees established in simulation hold on physical
hardware?") by measuring the same quantities the simulator reported, from real
packets, so the two can be compared line by line.

Run on the GATEWAY Pi:

    python3 fieldreport.py                 # last 24 hours
    python3 fieldreport.py --hours 168     # a week
    python3 fieldreport.py --md            # markdown, for REPORT.md §7.10
    python3 fieldreport.py --compare       # side by side with simulation

Nothing here modifies the database. It is safe to run while the network is up.
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))
sys.path.insert(0, os.path.join(_HERE, '..', 'gateway'))

import config
import db

# What the simulator predicted, for the comparison mode. Regenerate with
# tools/experiments.py if the design changes.
SIMULATED = {
    'collisions': 0,
    'delivery_pct': 100.0,
    'hops': {1: 1, 2: 2, 3: 2, 4: 2, 5: 3},
    'duty': {n: round(100 * config.duty_cycle_of(n), 1) for n in config.NODES},
}


def gather(conn, hours):
    since = time.time() - hours * 3600
    frames = hours * 3600.0 / config.FRAME_SECONDS

    rows = conn.execute("""
        SELECT node_id,
               COUNT(*)        AS packets,
               AVG(rssi)       AS rssi_mean,
               MIN(rssi)       AS rssi_min,
               MAX(rssi)       AS rssi_max,
               AVG(hops)       AS hops_mean,
               MIN(ts)         AS first_ts,
               MAX(ts)         AS last_ts,
               SUM(fire)       AS fires,
               SUM(CASE WHEN flags & 2 THEN 1 ELSE 0 END) AS sensor_errors,
               SUM(CASE WHEN flags & 4 THEN 1 ELSE 0 END) AS simulated
        FROM readings WHERE ts >= ?
        GROUP BY node_id ORDER BY node_id
    """, (since,)).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d['expected'] = frames
        d['pdr'] = 100.0 * d['packets'] / frames if frames else 0.0
        # Sequence gaps are the honest loss measure: the gateway cannot count
        # packets it never received, so PDR against expected frames is the
        # only view that includes total losses.
        d['via'] = _dominant_via(conn, d['node_id'], since)
        out.append(d)
    return out, frames


def _dominant_via(conn, node_id, since):
    row = conn.execute("""
        SELECT via, COUNT(*) n FROM readings
        WHERE node_id=? AND ts>=? GROUP BY via ORDER BY n DESC LIMIT 1
    """, (node_id, since)).fetchone()
    return row['via'] if row else None


def sequence_gaps(conn, node_id, hours):
    """Count losses from gaps in the origin's sequence numbers.

    Sequence numbers wrap at 256, so walk forward rather than subtracting.
    """
    since = time.time() - hours * 3600
    seqs = [r['seq'] for r in conn.execute(
        'SELECT seq FROM readings WHERE node_id=? AND ts>=? ORDER BY ts',
        (node_id, since)).fetchall() if r['seq'] is not None]
    if len(seqs) < 2:
        return 0, len(seqs)
    span = 1
    for a, b in zip(seqs, seqs[1:]):
        span += (b - a) % 256
    return max(0, span - len(seqs)), span


def report(conn, hours, markdown=False, compare=False, dbpath=None):
    rows, frames = gather(conn, hours)

    if not rows:
        print('No readings in the last %g hours. Is fire-gateway running?' % hours)
        return 1

    print('\n' + '=' * 78)
    print('FIELD MEASUREMENTS — last %g hours  (RQ6)' % hours)
    print('=' * 78)
    print('  frame length     : %d s   -> %.0f frames expected per node'
          % (config.FRAME_SECONDS, frames))
    print('  database         : %s' % (dbpath or config.DB_PATH))
    print()

    print('  %-8s %8s %7s %8s %8s %6s %6s %7s'
          % ('node', 'packets', 'PDR', 'rssi avg', 'rssi min', 'hops', 'via', 'lost'))
    print('  ' + '-' * 68)

    total_pkt = 0
    for r in rows:
        lost, span = sequence_gaps(conn, r['node_id'], hours)
        total_pkt += r['packets']
        print('  %-8s %8d %6.1f%% %8.1f %8d %6.1f %6s %7d'
              % ('node %d' % r['node_id'], r['packets'], r['pdr'],
                 r['rssi_mean'] or 0, r['rssi_min'] or 0,
                 r['hops_mean'], r['via'], lost))

    print('\n  totals: %d packets from %d nodes' % (total_pkt, len(rows)))

    # --- sensor health -----------------------------------------------------
    errs = sum(r['sensor_errors'] for r in rows)
    sims = sum(r['simulated'] for r in rows)
    fires = sum(r['fires'] for r in rows)
    print('  sensor errors    : %d (%.2f%% of packets)'
          % (errs, 100.0 * errs / total_pkt if total_pkt else 0))
    print('  simulated flags  : %d %s' % (sims,
          '(nodes still in SMOKE_MODE=simulate)' if sims else ''))
    print('  fire assertions  : %d' % fires)

    # --- silent nodes ------------------------------------------------------
    silent = sorted(set(config.NODES) - {r['node_id'] for r in rows})
    if silent:
        print('\n  NOT REPORTING    : %s' % silent)

    if compare:
        print('\n' + '-' * 78)
        print('  SIMULATED vs MEASURED')
        print('  %-22s %14s %14s %10s' % ('quantity', 'simulated', 'measured', 'verdict'))
        print('  ' + '-' * 64)

        meas_pdr = 100.0 * total_pkt / (frames * len(config.NODES))
        print('  %-22s %13.1f%% %13.1f%% %10s'
              % ('packet delivery', SIMULATED['delivery_pct'], meas_pdr,
                 'OK' if meas_pdr >= 95 else 'CHECK'))

        for r in rows:
            nid = r['node_id']
            exp = SIMULATED['hops'].get(nid)
            got = round(r['hops_mean'], 1)
            verdict = 'OK' if exp is not None and abs(got - exp) < 0.25 else 'DIFFERS'
            print('  %-22s %14s %14s %10s'
                  % ('node %d hop count' % nid, exp, got, verdict))

        print('\n  A hop count that differs from simulation means the radio')
        print('  topology is not what config.py assumes — usually CH-B being')
        print('  within gateway range. Re-survey with linktest.py.')

    if markdown:
        print('\n' + '=' * 78)
        print('MARKDOWN — paste into REPORT.md §7.10')
        print('=' * 78 + '\n')
        print('**Measured over %g hours** (%s)\n'
              % (hours, time.strftime('%Y-%m-%d')))
        print('| Node | Packets | PDR | RSSI mean | RSSI min | Hops | Via | Lost |')
        print('|---|---|---|---|---|---|---|---|')
        for r in rows:
            lost, _ = sequence_gaps(conn, r['node_id'], hours)
            print('| N-%02d | %d | %.1f%% | %.1f dBm | %d dBm | %.1f | %s | %d |'
                  % (r['node_id'], r['packets'], r['pdr'], r['rssi_mean'] or 0,
                     r['rssi_min'] or 0, r['hops_mean'], r['via'], lost))
        print()

    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hours', type=float, default=24)
    ap.add_argument('--md', action='store_true', help='markdown for the report')
    ap.add_argument('--compare', action='store_true', help='simulated vs measured')
    ap.add_argument('--db', help='database path (default from config)')
    args = ap.parse_args()

    path = args.db or config.DB_PATH
    if not os.path.exists(path):
        sys.exit('No database at %s — run this on the gateway Pi.' % path)

    conn = db.connect(path)
    try:
        sys.exit(report(conn, args.hours, args.md, args.compare, path))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
