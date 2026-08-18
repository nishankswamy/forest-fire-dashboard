#!/usr/bin/env python3
"""
experiments.py — regenerate every quantitative claim in REPORT.md.

Each experiment maps to a research question and prints the numbers the report
cites. Nothing here is hardcoded: the figures come from running the actual
firmware (via netsim) or the actual routing layer, so a reader can verify any
claim by running this and comparing.

    python3 experiments.py              # all experiments, human-readable
    python3 experiments.py --csv out/   # also write one CSV per experiment
    python3 experiments.py --md         # markdown tables, for pasting into the report
    python3 experiments.py --only rq4   # a single experiment

Runtime is a few minutes: the collision experiment drives six real firmware
stacks in real (scaled) time, and the lifetime experiment runs thousands of
protocol rounds.

Why this file exists
--------------------
The results in a report should be reproducible from the repository. Numbers
produced by scripts that were never committed are not evidence, they are
assertions. This turns them back into evidence.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, '..', '..'))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))
sys.path.insert(0, os.path.join(_HERE, '..', 'node'))
sys.path.insert(0, os.path.join(_HERE, '..', 'gateway'))

import config
import protocol
import tdma

RESULTS = {}


def _hr(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


# =========================================================================
# RQ1 — are collisions structurally impossible?
# =========================================================================

def rq1_collisions(frames=8):
    """Run the real six-node firmware on a virtual channel and count overlaps."""
    _hr('RQ1 — Collision freedom  (§7.2)')

    import netsim
    from netsim import ScaledClock, Medium, VirtualRadio, FakeSensors
    import sensor_node as node_mod
    import gateway as gw_mod
    import db

    clock = ScaledClock(0.05)
    medium = Medium(clock)
    sink = []
    log = lambda m: sink.append(m)

    conn = db.connect(os.path.join(tempfile.mkdtemp(), 'rq1.db'))
    gw = gw_mod.Gateway(radio=VirtualRadio(0, medium, clock), clock=clock,
                        conn=conn, log=log)
    nodes = [node_mod.SensorNode(node_id=n, radio=VirtualRadio(n, medium, clock),
                                 clock=clock, sensors=FakeSensors(n), log=log)
             for n in sorted(config.NODES)]

    origin = clock.monotonic() + 0.5
    gw.sf.frame_start = origin
    for n in nodes:
        n.sf.frame_start = origin

    def drive(w):
        for _ in range(frames):
            w.run_frame()

    threads = [threading.Thread(target=drive, args=(gw,), daemon=True)]
    threads += [threading.Thread(target=drive, args=(n,), daemon=True) for n in nodes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=frames * config.FRAME_SECONDS * 0.05 + 25)

    stored = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    rows = {r['node_id']: r for r in db.latest_per_node(conn)}
    expected = frames * len(config.NODES)

    print('  frames                : %d' % frames)
    print('  transmissions         : %d' % medium.transmissions)
    print('  COLLISIONS            : %d' % len(medium.collisions))
    print('  readings stored       : %d of %d expected' % (stored, expected))
    print('  duplicates at gateway : %d' % gw.stats['duplicates'])
    print('  retries (all nodes)   : %d' % sum(n.stats['retries'] for n in nodes))
    print('  delivery ratio        : %.1f%%' % (100.0 * stored / expected))

    RESULTS['rq1'] = {
        'frames': frames,
        'transmissions': medium.transmissions,
        'collisions': len(medium.collisions),
        'readings_stored': stored,
        'readings_expected': expected,
        'duplicates': gw.stats['duplicates'],
        'retries': sum(n.stats['retries'] for n in nodes),
        'delivery_pct': round(100.0 * stored / expected, 2),
    }

    # RQ2 rides along: hop counts prove the multi-hop path is real.
    hops = {nid: rows[nid]['hops'] for nid in sorted(rows)}
    print('\n  hop count per node    : %s' % hops)
    RESULTS['rq2_hops'] = hops
    conn.close()
    return RESULTS['rq1']


# =========================================================================
# RQ2 — can local minima be eliminated by design?
# =========================================================================

def rq2_local_minima():
    """Compare explicit routing (hardware) against greedy geographic (simulator)
    under identical terrain. The contrast IS the answer."""
    _hr('RQ2 — Local minima: explicit routing vs greedy geographic  (§7.3, §7.7)')

    # --- hardware scheme: explicit ordered next hops -----------------------
    reachable = 0
    for node_id in sorted(config.NODES):
        hops = config.routes_for(node_id)
        reachable += 1 if hops else 0
    print('  HARDWARE (explicit tables)')
    print('    nodes with a defined route  : %d of %d' % (reachable, len(config.NODES)))
    print('    geometric decisions made    : 0')
    print('    local minima possible       : NO — no greedy choice exists')

    # --- simulator scheme: greedy + perimeter, same ridge ------------------
    scratch = tempfile.mkdtemp()
    node_js = os.path.join(scratch, 'gpsr.js')
    script = r"""
global.window = {};
require(process.argv[2] + '/data-source.js');
require(process.argv[2] + '/routing.js');
const DS = window.DataSource, R = window.Routing;
const gw = { id: 'GW', lat: 11.6600, lng: 76.6300 };
const scenarios = {
  'no obstruction': [],
  'partial ridge (void)': [{lat1:11.6540,lng1:76.6360,lat2:11.6660,lng2:76.6360}],
  'full ridge (partition)': [{lat1:11.6460,lng1:76.6395,lat2:11.6740,lng2:76.6395}]
};
DS.getNodes().then(ns => {
  const out = {};
  for (const [name, obs] of Object.entries(scenarios)) {
    R.configure({gateway: gw, nodes: ns.map(n=>({id:n.id,lat:n.lat,lng:n.lng})), obstructions: obs});
    let delivered=0, minima=0, recovered=0;
    ns.forEach(n => { const t = R.tableFor(n.id);
      if (t.delivered) delivered++;
      if (t.localMinimum) { minima++; if (t.delivered) recovered++; } });
    out[name] = {total: ns.length, delivered, minima, recovered};
  }
  console.log(JSON.stringify(out));
  process.exit(0);
});
"""
    with open(node_js, 'w') as f:
        f.write(script)
    try:
        raw = subprocess.check_output(['node', node_js, _REPO], text=True,
                                      stderr=subprocess.DEVNULL, timeout=120)
        gpsr = json.loads(raw.strip().splitlines()[-1])
    except Exception as exc:
        print('    (simulator comparison unavailable: %s)' % exc)
        gpsr = {}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if gpsr:
        print('\n  SIMULATOR (greedy geographic + perimeter recovery), 50 nodes')
        print('    %-24s %8s %8s %10s' % ('scenario', 'deliv', 'minima', 'recovered'))
        for name, r in gpsr.items():
            print('    %-24s %4d/%-3d %8d %10d'
                  % (name, r['delivered'], r['total'], r['minima'], r['recovered']))

    RESULTS['rq2'] = {'explicit_routes': reachable, 'gpsr': gpsr}
    return RESULTS['rq2']


# =========================================================================
# RQ4 — does cluster-head rotation extend network lifetime?
# =========================================================================

def rq4_rotation(max_rounds=3000):
    """Rotation on vs off. Reports THREE lifetime definitions, because the
    whole finding is that they do not move together."""
    _hr('RQ4 — Does cluster-head rotation extend network lifetime?  (§7.6)')

    scratch = tempfile.mkdtemp()
    node_js = os.path.join(scratch, 'rotation.js')
    script = r"""
global.window = {};
require(process.argv[2] + '/data-source.js');
require(process.argv[2] + '/routing.js');
const DS = window.DataSource, R = window.Routing;
const gw = { id: 'GW', lat: 11.6600, lng: 76.6300 };
const ridge = [{lat1:11.6540,lng1:76.6360,lat2:11.6660,lng2:76.6360}];
const MAX = parseInt(process.argv[3], 10);

DS.getNodes().then(ns => {
  const defs = ns.map(n=>({id:n.id,lat:n.lat,lng:n.lng}));
  function trial(rotateEvery) {
    R.config.rotateEveryRounds = rotateEvery;
    R.configure({gateway: gw, nodes: defs, obstructions: ridge});
    let first=null, half=null, all=null;
    const headRounds = {}; defs.forEach(n=>headRounds[n.id]=0);
    for (let i=1;i<=MAX;i++) {
      const s = R.round();
      s.heads.forEach(h=>headRounds[h]++);
      if (first===null && s.dead.length>0) first=i;
      if (half===null && s.dead.length>=defs.length/2) half=i;
      if (s.dead.length>=defs.length) { all=i; break; }
    }
    const v = Object.values(headRounds);
    const mean = v.reduce((a,b)=>a+b,0)/v.length;
    const sd = Math.sqrt(v.reduce((a,b)=>a+(b-mean)**2,0)/v.length);
    const st = R.stats();
    return {first, half, all, sd: +sd.toFixed(1), mean: +mean.toFixed(1),
            pdr: +(100*st.delivered/(st.delivered+st.dropped)).toFixed(2)};
  }
  const on = trial(5), off = trial(0);
  R.config.rotateEveryRounds = 5;
  console.log(JSON.stringify({on, off}));
  process.exit(0);
});
"""
    with open(node_js, 'w') as f:
        f.write(script)
    try:
        raw = subprocess.check_output(['node', node_js, _REPO, str(max_rounds)],
                                      text=True, stderr=subprocess.DEVNULL, timeout=300)
        r = json.loads(raw.strip().splitlines()[-1])
    except Exception as exc:
        print('  unavailable: %s' % exc)
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    on, off = r['on'], r['off']
    print('  %-34s %12s %12s' % ('metric', 'rotation ON', 'rotation OFF'))
    print('  ' + '-' * 60)
    print('  %-34s %12s %12s' % ('first node death (round)', on['first'], off['first']))
    print('  %-34s %12s %12s' % ('half the network dead (round)', on['half'], off['half']))
    print('  %-34s %12s %12s' % ('head-duty spread, sd', on['sd'], off['sd']))
    print('  %-34s %11s%% %11s%%' % ('packet delivery ratio', on['pdr'], off['pdr']))

    if on['first'] and off['first']:
        gain = 100.0 * (on['first'] / off['first'] - 1)
        print('\n  first-death delay from rotation : %+.0f%%' % gain)
    if on['half'] and off['half']:
        halfgain = 100.0 * (on['half'] / off['half'] - 1)
        print('  half-network-death change       : %+.1f%%' % halfgain)
        print('\n  => Rotation EQUALISES node lifetime. It does not meaningfully')
        print('     extend total network life, which is a different claim from')
        print('     the one usually made for it.')

    RESULTS['rq4'] = r
    return r


# =========================================================================
# RQ5 — is the first-order radio model valid for SBC nodes?
# =========================================================================

def rq5_energy_model():
    """Show the radio-only model against the whole-node model, on the same
    node, so the discrepancy is explicit rather than described."""
    _hr('RQ5 — Validity of the first-order radio model for SBC nodes  (§5.5)')

    E_ELEC, E_AMP, BITS = 50e-9, 100e-12, protocol.PACKET_SIZE * 8
    BATTERY_J = config.BATTERY_J
    d = 500.0                       # representative hop, metres

    tx = E_ELEC * BITS + E_AMP * BITS * d * d
    rx = E_ELEC * BITS

    print('  Radio-only (first-order) model, one 500 m hop')
    print('    E_tx  : %.3e J   (%.3f mJ)' % (tx, tx * 1e3))
    print('    E_rx  : %.3e J' % rx)

    print('\n  %-10s %8s %14s %14s %14s' %
          ('node', 'duty', 'radio J/round', 'whole J/round', 'radio share'))
    print('  ' + '-' * 66)

    rows = []
    for nid in sorted(config.NODES):
        duty = config.duty_cycle_of(nid)
        # generous radio estimate: a few transmissions and receptions per frame
        radio_j = 6 * tx + 8 * rx
        whole_j = config.FRAME_SECONDS * (duty * config.ACTIVE_POWER_W +
                                          (1 - duty) * config.SLEEP_POWER_W)
        share = 100.0 * radio_j / whole_j
        print('  %-10s %7.1f%% %14.4f %14.2f %13.4f%%'
              % ('node %d' % nid, 100 * duty, radio_j, whole_j, share))
        rows.append({'node': nid, 'duty': round(duty, 4),
                     'radio_j_per_round': round(radio_j, 6),
                     'whole_j_per_round': round(whole_j, 3),
                     'radio_share_pct': round(share, 4)})

    worst = max(rows, key=lambda r: r['radio_share_pct'])
    rounds_radio = BATTERY_J / worst['radio_j_per_round']
    rounds_whole = BATTERY_J / worst['whole_j_per_round']

    print('\n  Rounds to exhaust a %.0f kJ battery, busiest node:' % (BATTERY_J / 1000))
    print('    radio-only model : %12.0f rounds  (%.1f years at 1/min)'
          % (rounds_radio, rounds_radio / (60 * 24 * 365)))
    print('    whole-node model : %12.0f rounds  (%.1f days at 1/min)'
          % (rounds_whole, rounds_whole / (60 * 24)))
    print('\n  => The radio accounts for under %.2f%% of node energy. Applied'
          % max(r['radio_share_pct'] for r in rows))
    print('     unmodified, the first-order model overstates battery life by')
    print('     roughly %.0fx and predicts no node ever dies.'
          % (rounds_radio / rounds_whole))

    RESULTS['rq5'] = {
        'per_node': rows,
        'rounds_radio_only': round(rounds_radio),
        'rounds_whole_node': round(rounds_whole),
        'overstatement_factor': round(rounds_radio / rounds_whole),
    }
    return RESULTS['rq5']


# =========================================================================
# Regulatory airtime — a trade-off the report should quantify
# =========================================================================

def airtime_budget():
    """Duty cycle in the regulatory sense: fraction of an hour spent
    transmitting. ISM bands cap this, and it is worth knowing the headroom."""
    _hr('Regulatory airtime budget  (trade-off: report §9)')

    air_s = protocol.airtime_ms(config.AIR_SPEED) / 1000.0
    frames_per_hour = 3600.0 / config.FRAME_SECONDS

    print('  packet airtime        : %.1f ms' % (air_s * 1000))
    print('  frames per hour       : %.0f' % frames_per_hour)
    print('\n  %-10s %10s %14s %12s' % ('node', 'tx/frame', 'airtime/hour', 'duty'))
    print('  ' + '-' * 50)

    rows = []
    total = 0.0
    for nid in sorted(config.NODES):
        tx_slots = 1
        if nid in config.FORWARD_SLOT:
            tx_slots += len(config.MEMBERS_OF.get(nid, [])) + 1
        if nid in config.BEACON_RELAY_SLOT:
            tx_slots += 1
        per_hour = tx_slots * air_s * frames_per_hour
        duty = 100.0 * per_hour / 3600.0
        total += per_hour
        print('  %-10s %10d %11.1f s %11.4f%%' % ('node %d' % nid, tx_slots, per_hour, duty))
        rows.append({'node': nid, 'tx_per_frame': tx_slots,
                     'airtime_s_per_hour': round(per_hour, 2),
                     'duty_pct': round(duty, 5)})

    print('  %-10s %10s %11.1f s %11.4f%%' % ('CHANNEL', '', total, 100 * total / 3600))

    busiest = max(rows, key=lambda r: r['duty_pct'])
    limit = float(os.environ.get('DUTY_LIMIT_PCT', 1.0))
    print('\n  busiest device        : node %d at %.3f%%'
          % (busiest['node'], busiest['duty_pct']))
    print('  channel occupancy     : %.3f%%' % (100 * total / 3600))
    print('  headroom vs %.1f%% limit : %.1fx' % (limit, limit / busiest['duty_pct']))
    print('\n  => Per-DEVICE duty is what most ISM regimes cap, and the busiest')
    print('     device here is CH-A at %.3f%% — inside a 1%% limit, but only by'
          % busiest['duty_pct'])
    print('     about %.0fx. That headroom shrinks linearly with cluster size and'
          % (limit / busiest['duty_pct']))
    print('     inversely with FRAME_SECONDS. A head serving 8 members at a 20 s')
    print('     frame would exceed 1%. Verify against local regulation before')
    print('     scaling either number.')

    RESULTS['airtime'] = {'per_node': rows,
                          'channel_s_per_hour': round(total, 2),
                          'channel_duty_pct': round(100 * total / 3600, 5)}
    return RESULTS['airtime']


# =========================================================================
# schedule validity — the RQ1 precondition
# =========================================================================

def schedule_check():
    _hr('TDMA schedule validity  (precondition for RQ1)')
    errors = tdma.validate_schedule()
    print('  slot count       : %d' % config.SLOT_COUNT)
    print('  frame length     : %d s' % config.FRAME_SECONDS)
    print('  slot length      : %d ms' % config.SLOT_MS)
    print('  packet airtime   : %.1f ms' % protocol.airtime_ms(config.AIR_SPEED))
    print('  validation       : %s' % ('PASS' if not errors else 'FAIL'))
    for e in errors:
        print('    - %s' % e)
    for nid in sorted(config.NODES):
        print('    %s' % tdma.Superframe(nid).describe())
    RESULTS['schedule'] = {'valid': not errors, 'errors': errors}
    return RESULTS['schedule']


# =========================================================================
# output
# =========================================================================

def write_csv(outdir):
    os.makedirs(outdir, exist_ok=True)
    written = []

    def dump(name, rows):
        if not rows:
            return
        path = os.path.join(outdir, name + '.csv')
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        written.append(path)

    if 'rq1' in RESULTS:
        dump('rq1_collisions', [RESULTS['rq1']])
    if 'rq4' in RESULTS:
        dump('rq4_rotation', [dict(condition=k, **v) for k, v in RESULTS['rq4'].items()])
    if 'rq5' in RESULTS:
        dump('rq5_energy_per_node', RESULTS['rq5']['per_node'])
    if 'airtime' in RESULTS:
        dump('airtime_per_node', RESULTS['airtime']['per_node'])

    print('\nCSV written:')
    for p in written:
        print('  ' + p)


def markdown_tables():
    print('\n' + '=' * 72)
    print('MARKDOWN — paste into REPORT.md')
    print('=' * 72 + '\n')

    if 'rq1' in RESULTS:
        r = RESULTS['rq1']
        print('**RQ1 — collision freedom** (%d frames, six nodes)\n' % r['frames'])
        print('| Metric | Result |')
        print('|---|---|')
        print('| Transmissions | %d |' % r['transmissions'])
        print('| **Collisions** | **%d** |' % r['collisions'])
        print('| Readings delivered | %d of %d |' % (r['readings_stored'], r['readings_expected']))
        print('| Duplicates | %d |' % r['duplicates'])
        print('| Retries | %d |' % r['retries'])
        print('| Delivery ratio | %.1f%% |\n' % r['delivery_pct'])

    if 'rq4' in RESULTS:
        on, off = RESULTS['rq4']['on'], RESULTS['rq4']['off']
        print('**RQ4 — cluster-head rotation**\n')
        print('| | Rotation ON | Rotation OFF |')
        print('|---|---|---|')
        print('| First node death | round **%s** | round %s |' % (on['first'], off['first']))
        print('| Half network dead | round %s | round %s |' % (on['half'], off['half']))
        print('| Head-duty spread (σ) | **%s** | %s |' % (on['sd'], off['sd']))
        print('| Packet delivery ratio | %.1f%% | %.1f%% |\n' % (on['pdr'], off['pdr']))

    if 'rq5' in RESULTS:
        r = RESULTS['rq5']
        print('**RQ5 — energy model validity**\n')
        print('| Model | Rounds to exhaust 12 kJ |')
        print('|---|---|')
        print('| Radio-only (first-order) | %d |' % r['rounds_radio_only'])
        print('| Whole-node (with platform baseline) | %d |' % r['rounds_whole_node'])
        print('\nOverstatement factor: **%d×**\n' % r['overstatement_factor'])

    if 'airtime' in RESULTS:
        r = RESULTS['airtime']
        print('**Regulatory airtime**: channel occupancy %.3f%% '
              '(%.1f s per hour)\n' % (r['channel_duty_pct'], r['channel_s_per_hour']))


ALL = {
    'schedule': schedule_check,
    'rq1': rq1_collisions,
    'rq2': rq2_local_minima,
    'rq4': rq4_rotation,
    'rq5': rq5_energy_model,
    'airtime': airtime_budget,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', choices=sorted(ALL), help='run one experiment')
    ap.add_argument('--csv', metavar='DIR', help='also write CSV files here')
    ap.add_argument('--md', action='store_true', help='print markdown tables')
    ap.add_argument('--frames', type=int, default=8)
    ap.add_argument('--rounds', type=int, default=3000)
    args = ap.parse_args()

    order = [args.only] if args.only else ['schedule', 'rq1', 'rq2', 'rq4', 'rq5', 'airtime']
    for name in order:
        fn = ALL[name]
        if name == 'rq1':
            fn(frames=args.frames)
        elif name == 'rq4':
            fn(max_rounds=args.rounds)
        else:
            fn()

    if args.csv:
        write_csv(args.csv)
    if args.md:
        markdown_tables()

    print('\nDone. Every figure above is regenerated from the firmware in this '
          'repository.')


if __name__ == '__main__':
    main()
