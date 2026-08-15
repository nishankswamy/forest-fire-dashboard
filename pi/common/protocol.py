"""
protocol.py — packet format, version 2. Keep IDENTICAL on every Pi.

Version 1 was a 12-byte one-way reading. Version 2 adds what multi-hop needs:
a packet type, an explicit next hop, the ORIGIN node (which survives being
forwarded, so the gateway still knows who actually took the measurement), a
hop count, and acknowledgements.

Wire format — 16 bytes, little-endian:

    off  size  field     meaning
    0    1     version   protocol version, currently 2
    1    1     type      1 BEACON, 2 DATA, 3 ACK
    2    1     src       who transmitted this frame
    3    1     dst       intended next hop (255 = broadcast)
    4    1     origin    node that produced the reading
    5    1     seq       origin's sequence number, wraps 0..255
    6    2     temp      int16, degrees C x 10
    8    1     hum       uint8, percent
    9    2     smoke     uint16, ppm
    11   1     batt      uint8, percent
    12   1     flags     bit0 fire, bit1 sensor error,
                         bit2 simulated, bit3 relayed
    13   1     hops      hops taken so far
    14   1     rsv       reserved, 0
    15   1     crc       CRC-8 over bytes 0..14

BEACON reuses the payload area — it carries no sensor data:

    off  size  field
    6    4     epoch     gateway unix time, low 32 bits
    10   1     frame_s   frame length in seconds
    11   1     slots     slot count in the superframe

At 2400 bps a 16-byte packet plus the module's own framing is ~77 ms on air,
which is why a 2000 ms slot is generous even with three retries.
"""

import struct
import time

VERSION = 2
PACKET_SIZE = 16

TYPE_BEACON = 1
TYPE_DATA = 2
TYPE_ACK = 3

FLAG_FIRE = 0x01
FLAG_SENSOR_ERROR = 0x02
FLAG_SIMULATED = 0x04
FLAG_RELAYED = 0x08

_HEAD = struct.Struct('<BBBBBB')          # version..seq, 6 bytes
_BODY = struct.Struct('<hBHBBBB')         # temp..rsv, 9 bytes
_BEACON = struct.Struct('<IBBBBB')        # epoch, frame_s, slots, pad*3


def crc8(data):
    """Dallas/Maxim CRC-8, polynomial 0x31. Catches the single-bit and burst
    errors that survive LoRa's own forward error correction."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _clamp(value, low, high):
    return max(low, min(high, value))


class BadPacket(Exception):
    """Bytes off the radio that are not a valid packet."""


def _finish(body):
    return body + bytes([crc8(body)])


def encode_data(src, dst, origin, seq, temp_c, humidity, smoke_ppm,
                battery_pct, fire=False, sensor_error=False, simulated=False,
                relayed=False, hops=0):
    """Build a DATA packet. Values are clamped, never raised on, so a flaky
    sensor can never take a node off the air."""
    flags = 0
    if fire:
        flags |= FLAG_FIRE
    if sensor_error:
        flags |= FLAG_SENSOR_ERROR
    if simulated:
        flags |= FLAG_SIMULATED
    if relayed:
        flags |= FLAG_RELAYED

    body = _HEAD.pack(VERSION, TYPE_DATA,
                      _clamp(int(src), 0, 255), _clamp(int(dst), 0, 255),
                      _clamp(int(origin), 1, 254), int(seq) & 0xFF)
    body += _BODY.pack(
        _clamp(int(round(temp_c * 10)), -32768, 32767),
        _clamp(int(round(humidity)), 0, 100),
        _clamp(int(round(smoke_ppm)), 0, 65535),
        _clamp(int(round(battery_pct)), 0, 100),
        flags,
        _clamp(int(hops), 0, 255),
        0)
    return _finish(body)


def encode_ack(src, dst, origin, seq):
    body = _HEAD.pack(VERSION, TYPE_ACK, int(src) & 0xFF, int(dst) & 0xFF,
                      int(origin) & 0xFF, int(seq) & 0xFF)
    body += _BODY.pack(0, 0, 0, 0, 0, 0, 0)
    return _finish(body)


def encode_beacon(src, frame_number, epoch=None, frame_seconds=60, slots=8):
    """Broadcast frame sync. Every node aligns its slot clock to this."""
    body = _HEAD.pack(VERSION, TYPE_BEACON, int(src) & 0xFF, 255,
                      0, int(frame_number) & 0xFF)
    body += _BEACON.pack(int(epoch if epoch is not None else time.time()) & 0xFFFFFFFF,
                         int(frame_seconds) & 0xFF, int(slots) & 0xFF, 0, 0, 0)
    return _finish(body)


def decode(raw):
    """Parse a packet. Raises BadPacket on wrong length, bad CRC, or an
    unsupported version."""
    if len(raw) != PACKET_SIZE:
        raise BadPacket('expected %d bytes, got %d' % (PACKET_SIZE, len(raw)))

    body, received_crc = raw[:-1], raw[-1]
    if crc8(body) != received_crc:
        raise BadPacket('CRC mismatch')

    version, ptype, src, dst, origin, seq = _HEAD.unpack(body[:6])
    if version != VERSION:
        raise BadPacket('unsupported protocol version %d' % version)

    out = {'type': ptype, 'src': src, 'dst': dst, 'origin': origin, 'seq': seq}

    if ptype == TYPE_BEACON:
        epoch, frame_s, slots, _a, _b, _c = _BEACON.unpack(body[6:])
        out.update({'epoch': epoch, 'frame_seconds': frame_s, 'slots': slots})
        return out

    if ptype == TYPE_ACK:
        return out

    if ptype != TYPE_DATA:
        raise BadPacket('unknown packet type %d' % ptype)

    temp, hum, smoke, batt, flags, hops, _rsv = _BODY.unpack(body[6:])
    if not 1 <= origin <= 254:
        raise BadPacket('invalid origin %d' % origin)

    out.update({
        'temp': temp / 10.0,
        'hum': float(hum),
        'smoke': float(smoke),
        'batt': float(batt),
        'flags': flags,
        'hops': hops,
        'fire': bool(flags & FLAG_FIRE),
        'sensor_error': bool(flags & FLAG_SENSOR_ERROR),
        'simulated': bool(flags & FLAG_SIMULATED),
        'relayed': bool(flags & FLAG_RELAYED),
    })
    return out


def airtime_ms(air_speed=2400, overhead_bytes=7):
    """Rough time on air for one packet, used to size TDMA slots."""
    return 1000.0 * (PACKET_SIZE + overhead_bytes) * 8 / float(air_speed)
