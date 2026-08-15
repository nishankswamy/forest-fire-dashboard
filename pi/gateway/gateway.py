#!/usr/bin/env python3
"""
gateway.py — runs on the GATEWAY Pi (LoRa addr 0, the one with internet).

Two jobs:

  1. Transmit the TDMA beacon in slot 0 of every frame. This is the clock the
     whole network aligns to; without it nodes free-run and slot boundaries
     eventually drift into each other.

  2. Receive DATA addressed to the gateway, acknowledge it, and write it to
     SQLite. Readings arrive either directly from CH-A, or relayed CH-B ->
     CH-A -> gateway, with the origin node preserved in the packet so the
     dashboard always attributes a measurement to the Pi that took it.

Run api.py alongside this to serve the dashboard.

    python3 gateway.py
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))
sys.path.insert(0, _HERE)

import config
import protocol
import tdma
import db

PRUNE_EVERY = 3600


class RealClock:
    def monotonic(self):
        return time.monotonic()

    def sleep_until(self, when):
        delay = when - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class Gateway:

    def __init__(self, radio=None, clock=None, conn=None, log=None):
        self.id = config.GATEWAY_ADDR
        self.clock = clock or RealClock()
        self.log = log or (lambda msg: print(msg, flush=True))

        if radio is None:
            import radio as radio_mod
            radio = radio_mod.Radio(self.id)
        self.radio = radio

        self.conn = conn if conn is not None else db.connect(config.DB_PATH)
        self.sf = tdma.Superframe(self.id)
        # The gateway is mains powered and is the time reference, so it stays
        # awake for the whole active portion of the frame.
        self.sf.active_slots = set(range(config.SLOT_COUNT))

        self.frame_number = 0
        self.last_prune = time.time()
        self.counts = {}
        self.stats = {'beacons': 0, 'received': 0, 'acked': 0, 'duplicates': 0}
        self._seen = set()          # (origin, seq) for duplicate suppression

    def send_beacon(self):
        packet = protocol.encode_beacon(
            src=self.id, frame_number=self.frame_number,
            epoch=int(time.time()),
            frame_seconds=config.FRAME_SECONDS, slots=config.SLOT_COUNT)
        self.radio.send(packet, config.BROADCAST_ADDR)
        self.stats['beacons'] += 1

    def store(self, packet, rssi):
        key = (packet['origin'], packet['seq'])
        if key in self._seen:
            # A retransmission whose ACK was lost. Acknowledge again so the
            # sender stops, but do not store it twice.
            self.stats['duplicates'] += 1
            return False
        self._seen.add(key)
        if len(self._seen) > 4096:
            self._seen = set(list(self._seen)[-1024:])

        db.insert(self.conn, {
            'node_id': packet['origin'],
            'temp': packet['temp'], 'hum': packet['hum'],
            'smoke': packet['smoke'], 'batt': packet['batt'],
            'fire': packet['fire'], 'seq': packet['seq'],
            'sensor_error': packet['sensor_error'],
            'simulated': packet['simulated'],
        }, rssi=rssi, via=packet['src'],
           # The packet counts hops taken before this one; add the final
           # transmission into the gateway so `hops` equals the number of
           # radio transmissions on the path (2 -> CH-A -> GW is 2).
           hops=packet['hops'] + 1)
        return True

    def handle(self, packet, rssi):
        if packet.get('type') != protocol.TYPE_DATA:
            return
        if packet.get('dst') != self.id:
            return

        self.radio.send(
            protocol.encode_ack(self.id, packet['src'],
                                packet['origin'], packet['seq']),
            packet['src'])
        self.stats['acked'] += 1

        if not self.store(packet, rssi):
            return
        self.stats['received'] += 1

        origin = packet['origin']
        self.counts[origin] = self.counts.get(origin, 0) + 1
        name = config.NODES.get(origin, {}).get('name', 'Node %d' % origin)
        status = config.evaluate(packet['temp'], packet['smoke'],
                                 packet['hum'], packet['batt'])

        self.log('[gw] %-22s seq=%3d %.1fC %.0f%%RH %.0fppm batt %.0f%% '
                 'via %d (%d hop%s) rssi %ddBm %s%s'
                 % (name, packet['seq'], packet['temp'], packet['hum'],
                    packet['smoke'], packet['batt'], packet['src'],
                    packet['hops'], '' if packet['hops'] == 1 else 's',
                    rssi, status.upper(),
                    '  *** FIRE ***' if packet['fire'] else ''))

    def run_frame(self):
        # ---- slot 0: beacon ----
        # Wake on the boundary, transmit after the guard band, so nodes that
        # wake on the same boundary are listening before the beacon goes out.
        self.clock.sleep_until(self.sf.slot_start(config.BEACON_SLOT))
        self.radio.wake()
        self.clock.sleep_until(self.sf.slot_window(config.BEACON_SLOT)[0])
        self.send_beacon()

        # ---- remaining slots: listen ----
        active_end = self.sf.slot_end(config.SLOT_COUNT - 1)
        while self.clock.monotonic() < active_end:
            for packet, rssi in self.radio.poll():
                self.handle(packet, rssi)
            self.clock.sleep_until(self.clock.monotonic() + 0.005)

        if time.time() - self.last_prune > PRUNE_EVERY:
            removed = db.prune(self.conn, config.RETENTION_DAYS)
            if removed:
                self.log('[gw] pruned %d rows older than %d days'
                         % (removed, config.RETENTION_DAYS))
            self.last_prune = time.time()

        self.clock.sleep_until(self.sf.frame_start + self.sf.frame_seconds)
        self.sf.advance_frame()
        self.frame_number = (self.frame_number + 1) & 0xFF

    def run(self):
        tdma.assert_schedule_ok()
        self.log('[gw] database: %s' % config.DB_PATH)
        self.log('[gw] beaconing every %ds, %d slots of %dms'
                 % (config.FRAME_SECONDS, config.SLOT_COUNT, config.SLOT_MS))
        try:
            while True:
                self.run_frame()
        except KeyboardInterrupt:
            self.log('\n[gw] stopped. per-node packets: %s' % self.counts)
            self.log('[gw] %s' % self.stats)
        finally:
            self.conn.close()


def main():
    Gateway().run()


if __name__ == '__main__':
    main()
