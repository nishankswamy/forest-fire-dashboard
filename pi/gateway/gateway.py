#!/usr/bin/env python3
"""
gateway.py — runs on the GATEWAY Raspberry Pi (the one with internet).

Listens on the LoRa radio, decodes packets from the sensor nodes, and writes
them to SQLite. Run api.py alongside it to serve the dashboard.

    python3 gateway.py

Requires Waveshare's driver next to this file:
    wget https://files.waveshare.com/upload/1/18/SX126X_LoRa_HAT_CODE.zip
    unzip SX126X_LoRa_HAT_CODE.zip
    cp SX126X_LoRa_HAT_Code/raspberrypi/python/sx126x.py .
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))

import config
import protocol
import db

PRUNE_EVERY = 3600           # seconds between retention sweeps
READ_POLL = 0.05             # serial poll interval


class Receiver:
    """Reads frames off the SX1262 HAT.

    The module hands us:  [src_hi, src_lo, freq_offset] + payload + [rssi]
    (it strips the destination header before transmitting). With RSSI enabled
    the last byte is 256 minus the absolute dBm value.
    """

    def __init__(self):
        self.radio = None
        self.buffer = bytearray()
        self.expected = 3 + protocol.PACKET_SIZE + 1   # header + packet + rssi

        try:
            import sx126x
        except ImportError:
            print('[warn] sx126x.py not found — no radio, gateway will idle. '
                  'See the header of this file for the download command.',
                  flush=True)
            return

        self.radio = sx126x.sx126x(
            serial_num=config.SERIAL_PORT,
            freq=config.FREQ,
            addr=config.GATEWAY_ADDR,
            power=config.POWER,
            rssi=True,
            air_speed=config.AIR_SPEED,
            net_id=config.NET_ID,
            crypt=config.CRYPT_KEY,
        )
        print('[gw] radio up: addr=%d freq=%d MHz air_speed=%d'
              % (config.GATEWAY_ADDR, config.FREQ, config.AIR_SPEED), flush=True)

    def poll(self):
        """Return a list of (reading, rssi) decoded this tick."""
        if self.radio is None:
            return []

        waiting = self.radio.ser.inWaiting()
        if waiting:
            self.buffer.extend(self.radio.ser.read(waiting))

        out = []
        # Frames are fixed length, so consume greedily. A corrupt frame is
        # dropped one byte at a time until the CRC lines up again — this
        # resynchronises without losing the frames stacked behind it.
        while len(self.buffer) >= self.expected:
            frame = bytes(self.buffer[:self.expected])
            src = (frame[0] << 8) + frame[1]
            payload = frame[3:3 + protocol.PACKET_SIZE]
            rssi_byte = frame[-1]

            try:
                reading = protocol.decode(payload)
            except protocol.BadPacket:
                del self.buffer[0]
                continue

            if reading['node_id'] != src:
                # Radio address and payload disagree — treat as corrupt.
                del self.buffer[0]
                continue

            del self.buffer[:self.expected]
            out.append((reading, -(256 - rssi_byte)))

        # Never let junk accumulate without bound.
        if len(self.buffer) > 4 * self.expected:
            del self.buffer[:-self.expected]

        return out


def main():
    conn = db.connect(config.DB_PATH)
    print('[gw] database: %s' % config.DB_PATH, flush=True)

    receiver = Receiver()
    last_prune = time.time()
    counts = {}

    try:
        while True:
            for reading, rssi in receiver.poll():
                db.insert(conn, reading, rssi=rssi)

                node_id = reading['node_id']
                name = config.NODES.get(node_id, {}).get('name', 'Node %d' % node_id)
                counts[node_id] = counts.get(node_id, 0) + 1

                status = config.evaluate(reading['temp'], reading['smoke'],
                                         reading['hum'], reading['batt'])
                marker = ' *** FIRE ***' if reading['fire'] else ''

                print('[gw] %-22s seq=%3d %.1fC %.0f%%RH %.0fppm batt %.0f%% '
                      'rssi %ddBm %s%s'
                      % (name, reading['seq'], reading['temp'], reading['hum'],
                         reading['smoke'], reading['batt'], rssi,
                         status.upper(), marker), flush=True)

            if time.time() - last_prune > PRUNE_EVERY:
                removed = db.prune(conn, config.RETENTION_DAYS)
                if removed:
                    print('[gw] pruned %d rows older than %d days'
                          % (removed, config.RETENTION_DAYS), flush=True)
                last_prune = time.time()

            time.sleep(READ_POLL)

    except KeyboardInterrupt:
        print('\n[gw] stopped. packets received: %s' % counts, flush=True)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
