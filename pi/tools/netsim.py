#!/usr/bin/env python3
"""
netsim.py — run the whole six-Pi network in one process, on a virtual radio.

This is the proof behind the two claims in ROUTING.md: no collisions, and no
local minima. It does not reimplement the protocol — it imports the *real*
gateway.py and sensor_node.py and runs them against a fake radio and a scaled
clock, so what is tested is the code that ships to the hardware.

The virtual medium models what actually matters:

  * Airtime. A transmission occupies the channel for protocol.airtime_ms().
  * Range. REACH below says who can hear whom. CH-B genuinely cannot hear the
    gateway, so it must relay through CH-A or nothing arrives.
  * Collisions. If two transmissions overlap in time and any node can hear
    both, that is recorded as a collision and both are corrupted — exactly
    what happens on a real shared channel.

Run:
    python3 netsim.py                 # 6 frames, normal operation
    python3 netsim.py --fire 5        # node 5 detects a fire
    python3 netsim.py --kill 1        # CH-A dies; watch route repair
"""

import argparse
import os
import random
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))
sys.path.insert(0, os.path.join(_HERE, '..', 'node'))
sys.path.insert(0, os.path.join(_HERE, '..', 'gateway'))

import config
import protocol
import tdma

# Who can hear whom. Deliberately NOT fully connected:
#   - the gateway cannot hear CH-B (4) or node 5, forcing a real relay
#   - node 2 cannot hear CH-B, so they are hidden terminals: carrier sense at
#     node 2 would report a clear channel while CH-B is transmitting. That is
#     the case CSMA cannot fix and TDMA does.
REACH = {
    0: {1, 2, 3},
    1: {0, 2, 3, 4, 5},
    2: {0, 1, 3},
    3: {0, 1, 2},
    4: {1, 5},
    5: {1, 4},
}

# Radio links are bidirectional. An asymmetric REACH is not "realistic" here,
# it is a bug: the data would arrive and the ACK would not, so the sender
# retries forever. Fail loudly rather than debug it later.
for _a, _peers in REACH.items():
    for _b in _peers:
        assert _a in REACH[_b], 'REACH asymmetric: %s hears %s but not back' % (_b, _a)


class ScaledClock:
    """Virtual time. SCALE=0.05 means one virtual second takes 50 ms."""

    def __init__(self, scale):
        self.scale = scale
        self.t0 = time.monotonic()

    def monotonic(self):
        return (time.monotonic() - self.t0) / self.scale

    def sleep_until(self, when):
        delay = (self.t0 + when * self.scale) - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class Medium:
    """The shared channel, and the collision detector."""

    def __init__(self, clock, loss_rate=0.0):
        self.clock = clock
        self.loss_rate = loss_rate
        self.lock = threading.Lock()
        self.active = []          # (start, end, src, receivers)
        self.collisions = []
        self.transmissions = 0
        self.inboxes = {}
        self.awake = {}           # node -> bool, for the energy accounting
        self.wake_time = {}

    def register(self, node_id):
        self.inboxes[node_id] = []
        self.awake[node_id] = False
        self.wake_time[node_id] = 0.0

    def transmit(self, src, packet, dst):
        now = self.clock.monotonic()
        end = now + protocol.airtime_ms(config.AIR_SPEED) / 1000.0
        receivers = REACH.get(src, set())

        with self.lock:
            self.transmissions += 1
            self.active = [t for t in self.active if t[1] > now]

            clashed = False
            for (o_start, o_end, o_src, o_rx) in self.active:
                if o_src == src:
                    continue
                # A collision only matters if some radio can hear both.
                if receivers & o_rx or o_src in receivers or src in o_rx:
                    self.collisions.append({
                        'at': round(now, 3), 'a': o_src, 'b': src,
                        'overlap_ms': round(1000 * (min(end, o_end) - now), 1),
                    })
                    clashed = True

            self.active.append((now, end, src, set(receivers)))

            if clashed:
                return           # both frames corrupted, nothing delivered

            for rx in receivers:
                if not self.awake.get(rx):
                    continue     # radio asleep — the whole point of duty cycling
                if self.loss_rate and random.random() < self.loss_rate:
                    continue
                # Delivered at the END of the transmission, not the start. A
                # receiver cannot decode a frame it has not finished hearing,
                # and modelling it otherwise lets a head acknowledge while the
                # sender is still keying up — which then reads as a collision.
                self.inboxes[rx].append((end, packet, -70 - random.randint(0, 30)))


class VirtualRadio:
    """Drop-in replacement for common/radio.Radio."""

    def __init__(self, addr, medium, clock, dead=False):
        self.addr = addr
        self.medium = medium
        self.clock = clock
        self.dead = dead
        self.awake = False
        self._awake_since = None
        self.awake_seconds = 0.0
        medium.register(addr)

    def wake(self):
        if self.awake:
            return
        self.awake = True
        self._awake_since = self.clock.monotonic()
        self.medium.awake[self.addr] = True

    def sleep(self):
        if not self.awake:
            return
        self.awake = False
        self.medium.awake[self.addr] = False
        if self._awake_since is not None:
            self.awake_seconds += self.clock.monotonic() - self._awake_since
            self._awake_since = None

    def send(self, packet, dst):
        if self.dead:
            return False
        self.wake()
        self.medium.transmit(self.addr, packet, dst)
        return True

    def poll(self):
        if self.dead:
            return []
        now = self.clock.monotonic()
        box = self.medium.inboxes[self.addr]
        out = []
        # Only frames whose airtime has elapsed are available to decode.
        ready = [item for item in box if item[0] <= now]
        for item in ready:
            box.remove(item)
            try:
                out.append((protocol.decode(item[1]), item[2]))
            except protocol.BadPacket:
                pass
        return out

    def recv_until(self, deadline, match=None):
        while self.clock.monotonic() < deadline:
            for decoded, rssi in self.poll():
                if match is None or match(decoded):
                    return decoded, rssi
            time.sleep(0.0005)
        return None

    def close(self):
        self.sleep()


class FakeSensors:
    """Deterministic readings, with an optional fire on one node."""

    def __init__(self, node_id, fire=False):
        self.node_id = node_id
        self.fire = fire
        self.n = 0

    def read_all(self, previous=None):
        self.n += 1
        if self.fire:
            return {'temp': 47.5, 'hum': 19.0, 'smoke': 380.0, 'batt': 88.0}, False
        return ({'temp': 24.0 + self.node_id * 0.4,
                 'hum': 55.0, 'smoke': 60.0,
                 'batt': 95.0 - self.node_id}, False)

    def is_simulated(self):
        return True


def run(frames=6, fire_node=None, kill_node=None, scale=0.05, loss=0.0, quiet=False):
    import sensor_node as node_mod
    import gateway as gw_mod
    import db

    errors = tdma.validate_schedule()
    if errors:
        print('SCHEDULE INVALID:', errors)
        return 1

    clock = ScaledClock(scale)
    medium = Medium(clock, loss_rate=loss)
    lines = []

    def log(msg):
        lines.append(msg)
        if not quiet:
            print(msg, flush=True)

    import tempfile
    dbpath = os.path.join(tempfile.mkdtemp(), 'netsim.db')
    conn = db.connect(dbpath)

    gw_radio = VirtualRadio(0, medium, clock)
    gateway = gw_mod.Gateway(radio=gw_radio, clock=clock, conn=conn, log=log)

    nodes = []
    for nid in sorted(config.NODES):
        radio = VirtualRadio(nid, medium, clock, dead=(nid == kill_node))
        nodes.append(node_mod.SensorNode(
            node_id=nid, radio=radio, clock=clock,
            sensors=FakeSensors(nid, fire=(nid == fire_node)), log=log))

    # Align every frame clock to the same origin, then let the beacon keep them
    # there. A real node starts unsynced and picks the beacon up on frame one.
    origin = clock.monotonic() + 0.5
    gateway.sf.frame_start = origin
    for n in nodes:
        n.sf.frame_start = origin

    def drive(worker, count):
        for _ in range(count):
            worker.run_frame()

    threads = [threading.Thread(target=drive, args=(gateway, frames), daemon=True)]
    threads += [threading.Thread(target=drive, args=(n, frames), daemon=True) for n in nodes]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=frames * config.FRAME_SECONDS * scale + 20)

    # ---- results ----
    print('\n' + '=' * 68)
    print('NETSIM — %d frames, %d nodes + gateway' % (frames, len(nodes)))
    if fire_node:
        print('  fire injected at node %d' % fire_node)
    if kill_node:
        print('  node %d killed (radio dead)' % kill_node)
    print('=' * 68)

    print('\n--- COLLISIONS ---')
    print('  transmissions : %d' % medium.transmissions)
    print('  collisions    : %d' % len(medium.collisions))
    if medium.collisions:
        for c in medium.collisions[:8]:
            print('    t=%.2fs  node %s vs node %s  overlap %.1f ms'
                  % (c['at'], c['a'], c['b'], c['overlap_ms']))

    print('\n--- DELIVERY (rows reaching the gateway DB) ---')
    rows = db.latest_per_node(conn)
    got = {r['node_id']: r for r in rows}
    total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    print('  rows stored   : %d' % total)
    for nid in sorted(config.NODES):
        r = got.get(nid)
        if r:
            print('    node %d  via %-4s hops=%d  %.1fC %.0fppm  %s'
                  % (nid, r['via'], r['hops'], r['temp'], r['smoke'],
                     'FIRE' if r['fire'] else ''))
        else:
            print('    node %d  NOTHING RECEIVED' % nid)

    print('\n--- ROUTES OBSERVED ---')
    for r in db.route_summary(conn):
        print('    node %d  via %s  hops=%d  packets=%d'
              % (r['node_id'], r['via'], r['hops'], r['n']))

    print('\n--- ENERGY (radio awake time) ---')
    frame_total = frames * config.FRAME_SECONDS
    for n in nodes:
        n.radio.sleep()
        pct = 100.0 * n.radio.awake_seconds / frame_total
        print('    node %d  awake %5.1fs / %ds = %4.1f%%   (design %.1f%%)'
              % (n.id, n.radio.awake_seconds, frame_total, pct,
                 100 * config.duty_cycle_of(n.id)))

    print('\n--- PER NODE ---')
    for n in nodes:
        print('    node %d %-5s %s' % (n.id, n.role, n.stats))
    print('    gateway     %s' % gateway.stats)

    conn.close()

    ok = len(medium.collisions) == 0

    # Only require delivery from nodes that still have a PHYSICAL path to the
    # gateway once the dead node is removed. If killing CH-A partitions
    # cluster B off the map, buffering is the correct behaviour and demanding
    # delivery would be asserting something impossible.
    def reachable_from(start, dead):
        seen, queue = {start}, [start]
        while queue:
            cur = queue.pop(0)
            for peer in REACH.get(cur, set()):
                if peer in dead or peer in seen:
                    continue
                seen.add(peer)
                queue.append(peer)
        return seen

    dead = {kill_node} if kill_node else set()
    connected = reachable_from(config.GATEWAY_ADDR, dead)
    expected = (set(config.NODES) - dead) & connected
    partitioned = (set(config.NODES) - dead) - connected
    delivered = set(got)
    missing = expected - delivered

    if partitioned:
        print('  partitioned (no physical path, buffering is correct): %s'
              % sorted(partitioned))

    print('\n' + '=' * 68)
    print('  collisions        : %s' % ('PASS (0)' if ok else 'FAIL (%d)' % len(medium.collisions)))
    print('  all nodes reached : %s' % ('PASS' if not missing else 'FAIL, missing %s' % sorted(missing)))
    print('=' * 68)
    return 0 if (ok and not missing) else 1


def main():
    ap = argparse.ArgumentParser(description='Six-Pi network simulator.')
    ap.add_argument('--frames', type=int, default=6)
    ap.add_argument('--fire', type=int, default=None, help='node id that detects fire')
    ap.add_argument('--kill', type=int, default=None, help='node id whose radio is dead')
    ap.add_argument('--scale', type=float, default=0.05,
                    help='virtual-to-real time scale (0.05 = 20x faster)')
    ap.add_argument('--loss', type=float, default=0.0, help='per-packet loss probability')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    sys.exit(run(args.frames, args.fire, args.kill, args.scale, args.loss, args.quiet))


if __name__ == '__main__':
    main()
