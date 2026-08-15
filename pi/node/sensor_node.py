#!/usr/bin/env python3
"""
sensor_node.py — runs on each SENSOR Raspberry Pi.

Reads the sensors, decides locally whether it is looking at a fire, and
transmits a 12-byte packet to the gateway over LoRa.

Run:
    NODE_ID=2 SMOKE_MODE=simulate DHT_MODE=simulate python3 sensor_node.py

Once real sensors are wired:
    NODE_ID=2 SMOKE_MODE=adc DHT_MODE=dht22 python3 sensor_node.py

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
import sensors

# Fire must persist for this many consecutive cycles before the node asserts
# the fire flag. A single spurious reading — someone lighting a cigarette
# under the sensor — should not dispatch a fire crew.
FIRE_CONFIRM_CYCLES = int(os.environ.get('FIRE_CONFIRM_CYCLES', 2))


def build_radio():
    """Open the SX1262 HAT. Returns None if the driver or hardware is absent,
    so you can develop this script on a laptop."""
    try:
        import sx126x
    except ImportError:
        print('[warn] sx126x.py not found — running in DRY RUN mode, '
              'packets will be printed instead of transmitted', flush=True)
        return None

    return sx126x.sx126x(
        serial_num=config.SERIAL_PORT,
        freq=config.FREQ,
        addr=config.NODE_ID,
        power=config.POWER,
        rssi=True,
        air_speed=config.AIR_SPEED,
        net_id=config.NET_ID,
        crypt=config.CRYPT_KEY,
    )


def frame_for_gateway(payload: bytes) -> bytes:
    """Wrap our packet in the Waveshare address header.

    The module expects:
        [dest_hi, dest_lo, dest_freq_offset, src_hi, src_lo, src_freq_offset] + payload

    The frequency offset is the channel number relative to the 850 MHz base
    for this band — 866 MHz becomes offset 16.
    """
    offset = config.FREQ - 850
    dest, src = config.GATEWAY_ADDR, config.NODE_ID
    header = bytes([
        (dest >> 8) & 0xFF, dest & 0xFF, offset,
        (src >> 8) & 0xFF, src & 0xFF, offset,
    ])
    return header + payload


def main():
    node_id = config.NODE_ID
    name = config.NODES.get(node_id, {}).get('name', 'Node %d' % node_id)
    print('[node] starting %s (id=%d) freq=%d MHz interval=%ds'
          % (name, node_id, config.FREQ, config.UPLINK_INTERVAL), flush=True)
    print('[node] smoke=%s dht=%s' % (sensors.SMOKE_MODE, sensors.DHT_MODE), flush=True)

    radio = build_radio()
    seq = 0
    previous = None
    fire_streak = 0

    try:
        while True:
            started = time.time()

            reading, sensor_error = sensors.read_all(previous)
            previous = reading

            status = config.evaluate(reading['temp'], reading['smoke'],
                                     reading['hum'], reading['batt'])
            fire_streak = fire_streak + 1 if status == 'fire' else 0
            fire_confirmed = fire_streak >= FIRE_CONFIRM_CYCLES

            packet = protocol.encode(
                node_id=node_id,
                seq=seq,
                temp_c=reading['temp'],
                humidity=reading['hum'],
                smoke_ppm=reading['smoke'],
                battery_pct=reading['batt'],
                fire=fire_confirmed,
                sensor_error=sensor_error,
                simulated=sensors.is_simulated(),
            )

            line = ('[node] seq=%3d  %.1fC  %.0f%%RH  %.0fppm  batt %.0f%%  %s%s'
                    % (seq, reading['temp'], reading['hum'], reading['smoke'],
                       reading['batt'], status.upper(),
                       '  (sensor error)' if sensor_error else ''))
            print(line, flush=True)

            if radio is not None:
                radio.send(frame_for_gateway(packet))
            else:
                print('[dry-run] would send %s' % packet.hex(), flush=True)

            # A confirmed fire uplinks every 10 s instead of every minute —
            # the dashboard should track a spreading fire in near real time.
            interval = 10 if fire_confirmed else config.UPLINK_INTERVAL
            seq = (seq + 1) & 0xFF

            elapsed = time.time() - started
            time.sleep(max(1.0, interval - elapsed))

    except KeyboardInterrupt:
        print('\n[node] stopped', flush=True)


if __name__ == '__main__':
    main()
