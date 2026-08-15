#!/usr/bin/env python3
"""
linktest.py — prove a radio link between two Pis before anything else.

Run this before wiring a single sensor, and again at every candidate mounting
point. It bypasses TDMA entirely and just hammers packets down the link, so a
clean result means the radio, jumpers, serial port, frequency, net id and
crypt key are all correct, and any later problem is a protocol or sensor
problem rather than a physical one.

    # on the RECEIVER (usually the gateway, addr 0)
    python3 linktest.py rx

    # on the TRANSMITTER
    python3 linktest.py tx --node-id 2 --count 100

What you want to see:

    delivered  >= 99%       at close range with line of sight
    crc_fail   0            anything else is interference or a bad antenna
    rssi       > -100 dBm   weaker than -110 and you are at the edge

This doubles as a site survey. Walk the forest, run a 50-packet burst at each
candidate position, and keep the spots that deliver. Do that BEFORE mounting
anything permanently — moving a Pi is cheap now and expensive later.

The links this network actually needs:

    gateway(0) <-> CH-A(1)          CH-A(1) <-> node 2, node 3
    CH-A(1)    <-> CH-B(4)          CH-B(4) <-> node 5

CH-B is expected NOT to reach the gateway. If it does, your placement is not
exercising the relay and you should move it further out.
"""

import argparse
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))

import config
import protocol
import radio as radio_mod


def banner(role, addr):
    print('[linktest:%s] addr=%d freq=%d MHz air=%d net=%d port=%s'
          % (role, addr, config.FREQ, config.AIR_SPEED,
             config.NET_ID, config.SERIAL_PORT), flush=True)
    print('[linktest:%s] every Pi must agree on all of those or you see nothing'
          % role, flush=True)


# ----------------------------------------------------------------- transmit --

def run_tx(args):
    banner('tx', args.node_id)
    print('[linktest:tx] sending %d packets to addr %d, %.1fs apart'
          % (args.count, args.dst, args.interval), flush=True)

    link = radio_mod.Radio(args.node_id)
    link.wake()
    sent = 0

    try:
        for i in range(args.count):
            # Fixed, recognisable values. The receiver checks them, so a packet
            # that passes CRC but arrives mangled is still caught.
            packet = protocol.encode_data(
                src=args.node_id, dst=args.dst, origin=args.node_id,
                seq=i & 0xFF, temp_c=25.0, humidity=50, smoke_ppm=100,
                battery_pct=90, simulated=True)

            link.send(packet, args.dst)
            sent += 1

            if sent % 10 == 0 or sent == args.count:
                print('[linktest:tx] sent %d/%d' % (sent, args.count), flush=True)
            if i < args.count - 1:
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print('\n[linktest:tx] interrupted', flush=True)
    finally:
        link.close()

    print('[linktest:tx] done — %d transmitted. Ctrl-C the receiver for the score.'
          % sent, flush=True)


# ------------------------------------------------------------------ receive --

class Stats:
    def __init__(self):
        self.received = 0
        self.crc_fail = 0
        self.mismatched = 0
        self.rssi = []
        self.seqs = []


def run_rx(args):
    banner('rx', args.addr)
    print('[linktest:rx] listening — Ctrl-C for the summary', flush=True)

    link = radio_mod.Radio(args.addr)
    link.wake()
    per_node = {}

    try:
        while True:
            for packet, rssi in link.poll():
                if packet.get('type') != protocol.TYPE_DATA:
                    continue
                node = per_node.setdefault(packet['origin'], Stats())
                node.received += 1
                node.rssi.append(rssi)
                node.seqs.append(packet['seq'])

                good = (abs(packet['temp'] - 25.0) < 0.05 and packet['hum'] == 50
                        and packet['smoke'] == 100 and packet['batt'] == 90)
                if not good:
                    node.mismatched += 1

                print('[linktest:rx] node %-3d seq=%3d rssi=%4d dBm  %s'
                      % (packet['origin'], packet['seq'], rssi,
                         'ok' if good else 'PAYLOAD MISMATCH'), flush=True)
            time.sleep(0.02)

    except KeyboardInterrupt:
        print('\n', flush=True)
        summarise(per_node)
    finally:
        link.close()


def summarise(per_node):
    if not per_node:
        print('[linktest:rx] NOTHING RECEIVED.')
        print()
        print('  Check in this order:')
        print('    1. UART jumper on position B, and the M0/M1 caps REMOVED')
        print('    2. FREQ / AIR_SPEED / NET_ID / CRYPT_KEY identical on both Pis')
        print('    3. Wrong serial device — ls -l /dev/serial*')
        print('    4. Serial login console still holding the port')
        print('       (pi/setup.sh disables it; check with systemctl status serial-getty@ttyS0)')
        print('    5. Antenna actually screwed on, at both ends')
        return

    print('=' * 64)
    print('%-6s %8s %8s %10s %9s %10s'
          % ('node', 'recv', 'lost', 'delivered', 'crc_fail', 'rssi_mean'))
    print('-' * 64)

    for node_id in sorted(per_node):
        s = per_node[node_id]
        if not s.seqs:
            continue
        # Sequence numbers wrap at 256, so walk forward rather than
        # subtracting first from last.
        span = 1
        for a, b in zip(s.seqs, s.seqs[1:]):
            span += (b - a) % 256
        lost = max(0, span - len(s.seqs))
        delivered = 100.0 * len(s.seqs) / span if span else 0.0

        print('%-6d %8d %8d %9.1f%% %9d %10.1f'
              % (node_id, s.received, lost, delivered, s.crc_fail,
                 statistics.mean(s.rssi)))
        if s.mismatched:
            print('       %d packet(s) passed CRC but carried unexpected values'
                  % s.mismatched)

    print('=' * 64)
    for node_id in sorted(per_node):
        s = per_node[node_id]
        if not s.rssi:
            continue
        worst = min(s.rssi)
        print('node %d: rssi min %d / max %d dBm' % (node_id, worst, max(s.rssi)))
        if worst < -110:
            print('  -> marginal. Raise the antenna, keep it clear of metal,')
            print('     and confirm AIR_SPEED is 2400 for maximum range.')
        elif worst < -100:
            print('  -> usable on a bench, risky in the field.')
        else:
            print('  -> healthy.')


# --------------------------------------------------------------------- cli --

def main():
    ap = argparse.ArgumentParser(
        description='Point-to-point LoRa link test. Run rx first, then tx.')
    sub = ap.add_subparsers(dest='role', required=True)

    tx = sub.add_parser('tx', help='transmit test packets')
    tx.add_argument('--node-id', type=int, default=config.NODE_ID)
    tx.add_argument('--dst', type=int, default=config.GATEWAY_ADDR,
                    help='address to send to (default: %(default)s)')
    tx.add_argument('--count', type=int, default=50)
    tx.add_argument('--interval', type=float, default=1.0)

    rx = sub.add_parser('rx', help='receive and score')
    rx.add_argument('--addr', type=int, default=config.GATEWAY_ADDR,
                    help='address to listen as (default: %(default)s)')

    args = ap.parse_args()
    if args.role == 'tx':
        if not 1 <= args.node_id <= 254:
            sys.exit('node id must be 1..254 (0 is the gateway)')
        run_tx(args)
    else:
        run_rx(args)


if __name__ == '__main__':
    main()
