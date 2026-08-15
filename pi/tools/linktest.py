#!/usr/bin/env python3
"""
linktest.py — prove the LoRa link between two Pis before anything else.

Run this before wiring a single sensor. It uses the real protocol.py framing,
so a clean result here means the radio, jumpers, serial port, frequency, net id
and crypt key are all correct, and any later problem is a sensor problem.

    # on the GATEWAY Pi (addr 0)
    python3 linktest.py rx

    # on a SENSOR Pi
    python3 linktest.py tx --node-id 1 --count 100

Then read the summary. What you want to see:

    delivered   >= 99%      at close range with a clear line of sight
    crc_fail    0           anything else means interference or a bad antenna
    rssi        > -100 dBm  weaker than -110 and you are near the edge

Move the nodes to their real positions and run it again. This is also how you
survey placement: walk the site, run a 50-packet burst at each candidate spot,
and keep the ones that deliver.
"""

import argparse
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))
# sx126x.py is installed next to whichever script needs it, so look in both.
sys.path.insert(0, os.path.join(_HERE, '..', 'node'))
sys.path.insert(0, os.path.join(_HERE, '..', 'gateway'))

import config
import protocol


def open_radio(addr):
    """Open the HAT at the given address, or exit with a useful message."""
    try:
        import sx126x
    except ImportError:
        sys.exit(
            'sx126x.py not found.\n'
            'Run pi/setup.sh, or fetch it manually:\n'
            '  wget https://files.waveshare.com/upload/1/18/SX126X_LoRa_HAT_CODE.zip\n'
            '  unzip SX126X_LoRa_HAT_CODE.zip\n'
            '  cp SX126X_LoRa_HAT_Code/raspberrypi/python/sx126x.py pi/node/')

    return sx126x.sx126x(
        serial_num=config.SERIAL_PORT,
        freq=config.FREQ,
        addr=addr,
        power=config.POWER,
        rssi=True,
        air_speed=config.AIR_SPEED,
        net_id=config.NET_ID,
        crypt=config.CRYPT_KEY,
    )


def banner(role):
    print('[linktest:%s] freq=%d MHz air_speed=%d net_id=%d port=%s'
          % (role, config.FREQ, config.AIR_SPEED, config.NET_ID, config.SERIAL_PORT),
          flush=True)
    print('[linktest:%s] every Pi must agree on all four, or you will see nothing'
          % role, flush=True)


# ----------------------------------------------------------------- transmit --

def frame_for_gateway(payload):
    """Same wrapping sensor_node.py uses — dest/src address headers."""
    offset = config.FREQ - 850
    dest, src = config.GATEWAY_ADDR, config.NODE_ID
    return bytes([
        (dest >> 8) & 0xFF, dest & 0xFF, offset,
        (src >> 8) & 0xFF, src & 0xFF, offset,
    ]) + payload


def run_tx(args):
    config.NODE_ID = args.node_id
    banner('tx')
    print('[linktest:tx] node_id=%d sending %d packets every %.1fs'
          % (args.node_id, args.count, args.interval), flush=True)

    radio = open_radio(args.node_id)
    sent = 0

    try:
        for i in range(args.count):
            seq = i & 0xFF
            # Fixed, recognisable values — the receiver checks these, so a
            # corrupted-but-valid-CRC packet still gets caught.
            packet = protocol.encode(
                node_id=args.node_id, seq=seq,
                temp_c=25.0, humidity=50, smoke_ppm=100, battery_pct=90,
                fire=False, sensor_error=False, simulated=True)

            radio.send(frame_for_gateway(packet))
            sent += 1

            if sent % 10 == 0 or sent == args.count:
                print('[linktest:tx] sent %d/%d' % (sent, args.count), flush=True)

            if i < args.count - 1:
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print('\n[linktest:tx] interrupted', flush=True)

    print('[linktest:tx] done — %d packets transmitted' % sent, flush=True)
    print('[linktest:tx] now read the summary on the gateway (Ctrl-C there)', flush=True)


# ------------------------------------------------------------------ receive --

class Stats:
    def __init__(self):
        self.received = 0
        self.crc_fail = 0
        self.mismatched = 0        # payload values not what tx sends
        self.rssi = []
        self.seqs = []
        self.first_ts = None
        self.last_ts = None


def run_rx(args):
    banner('rx')
    print('[linktest:rx] listening as addr %d — Ctrl-C for the summary'
          % config.GATEWAY_ADDR, flush=True)

    radio = open_radio(config.GATEWAY_ADDR)
    frame_len = 3 + protocol.PACKET_SIZE + 1
    buffer = bytearray()
    per_node = {}

    try:
        while True:
            waiting = radio.ser.inWaiting()
            if waiting:
                buffer.extend(radio.ser.read(waiting))

            while len(buffer) >= frame_len:
                frame = bytes(buffer[:frame_len])
                src = (frame[0] << 8) + frame[1]
                payload = frame[3:3 + protocol.PACKET_SIZE]
                rssi = -(256 - frame[-1])

                try:
                    reading = protocol.decode(payload)
                except protocol.BadPacket:
                    # Resync one byte at a time, exactly as gateway.py does.
                    node = per_node.setdefault(src, Stats())
                    node.crc_fail += 1
                    del buffer[0]
                    continue

                del buffer[:frame_len]

                node = per_node.setdefault(reading['node_id'], Stats())
                node.received += 1
                node.rssi.append(rssi)
                node.seqs.append(reading['seq'])
                now = time.time()
                node.first_ts = node.first_ts or now
                node.last_ts = now

                # The transmitter sends fixed values; anything else means the
                # payload survived CRC but is not our test packet.
                good = (abs(reading['temp'] - 25.0) < 0.05 and reading['hum'] == 50
                        and reading['smoke'] == 100 and reading['batt'] == 90)
                if not good:
                    node.mismatched += 1

                print('[linktest:rx] node %-3d seq=%3d rssi=%4d dBm  %s'
                      % (reading['node_id'], reading['seq'], rssi,
                         'ok' if good else 'PAYLOAD MISMATCH'),
                      flush=True)

            if len(buffer) > 4 * frame_len:
                del buffer[:-frame_len]

            time.sleep(0.05)

    except KeyboardInterrupt:
        print('\n', flush=True)
        summarise(per_node)


def summarise(per_node):
    if not per_node:
        print('[linktest:rx] NOTHING RECEIVED.')
        print()
        print('  Most likely causes, in the order worth checking:')
        print('    1. UART jumper not on position B, or M0/M1 caps still fitted')
        print('    2. FREQ / AIR_SPEED / NET_ID / CRYPT_KEY differ between the Pis')
        print('    3. Wrong serial device — check: ls -l /dev/serial*')
        print('    4. Serial login console still holding the port')
        return

    print('=' * 62)
    print('%-6s %8s %8s %9s %9s %10s' % ('node', 'recv', 'lost', 'delivered', 'crc_fail', 'rssi_mean'))
    print('-' * 62)

    for node_id in sorted(per_node):
        s = per_node[node_id]
        if not s.seqs:
            print('%-6d %8d %8s %9s %9d %10s'
                  % (node_id, 0, '-', '-', s.crc_fail, '-'))
            continue

        # Sequence numbers wrap at 256, so count the span by walking forward
        # rather than subtracting first from last.
        span = 1
        for a, b in zip(s.seqs, s.seqs[1:]):
            span += (b - a) % 256
        lost = max(0, span - len(s.seqs))
        delivered = 100.0 * len(s.seqs) / span if span else 0.0

        print('%-6d %8d %8d %8.1f%% %9d %10.1f'
              % (node_id, s.received, lost, delivered, s.crc_fail,
                 statistics.mean(s.rssi)))

        if s.mismatched:
            print('       %d packet(s) passed CRC but carried unexpected values' % s.mismatched)

    print('=' * 62)

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
            print('  -> usable but not comfortable. Fine on a bench, risky in the field.')
        else:
            print('  -> healthy.')


# --------------------------------------------------------------------- cli --

def main():
    parser = argparse.ArgumentParser(
        description='LoRa link test for the forest-fire network.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run rx on the gateway first, then tx on a sensor Pi.')
    sub = parser.add_subparsers(dest='role', required=True)

    tx = sub.add_parser('tx', help='transmit test packets (run on a sensor Pi)')
    tx.add_argument('--node-id', type=int, default=config.NODE_ID,
                    help='node id to transmit as (default: %(default)s)')
    tx.add_argument('--count', type=int, default=50,
                    help='packets to send (default: %(default)s)')
    tx.add_argument('--interval', type=float, default=1.0,
                    help='seconds between packets (default: %(default)s)')

    sub.add_parser('rx', help='receive and score (run on the gateway Pi)')

    args = parser.parse_args()

    if args.role == 'tx':
        if not 1 <= args.node_id <= 254:
            sys.exit('node id must be 1..254 (0 is the gateway)')
        run_tx(args)
    else:
        run_rx(args)


if __name__ == '__main__':
    main()
