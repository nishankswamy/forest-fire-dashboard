"""
protocol.py — packet format shared by sensor nodes and the gateway.

Keep this file IDENTICAL on every Pi. Copy it to each node.

Why binary and not JSON: the SX1262 HAT defaults to 2400 bps air speed.
A JSON reading is ~90 bytes (~300 ms on air); this is 12 bytes (~40 ms).
Shorter time-on-air means less collision risk and less battery per uplink.

Wire format (12 bytes, little-endian):

    offset  size  field      encoding
    0       1     version    protocol version, currently 1
    1       1     node_id    1..254  (0 is reserved for the gateway)
    2       1     seq        rolls over 0..255, used to spot lost packets
    3       2     temp       int16, degrees C x 10   (-3276.8 .. 3276.7)
    5       1     hum        uint8, percent 0..100
    6       2     smoke      uint16, ppm 0..65535
    8       1     batt       uint8, percent 0..100
    9       1     flags      bit0 = node's own fire verdict
                             bit1 = sensor read error this cycle
                             bit2 = running on simulated values
    10      1     reserved   0 for now
    11      1     crc        CRC-8 over bytes 0..10

The Waveshare driver adds its own 6-byte address/frequency header in front
and one RSSI byte behind, so the on-air frame is 19 bytes total.
"""

import struct

VERSION = 1
PACKET_SIZE = 12
_STRUCT = struct.Struct('<BBBhBHBBB')  # 11 bytes, crc appended separately

FLAG_FIRE = 0x01
FLAG_SENSOR_ERROR = 0x02
FLAG_SIMULATED = 0x04


def crc8(data: bytes) -> int:
    """Dallas/Maxim CRC-8, polynomial 0x31 reflected. Catches the single-bit
    and burst errors that survive LoRa's own FEC."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _clamp(value, low, high):
    return max(low, min(high, value))


def encode(node_id, seq, temp_c, humidity, smoke_ppm, battery_pct,
           fire=False, sensor_error=False, simulated=False) -> bytes:
    """Build a 12-byte packet. Values are clamped, never raised on, so a
    flaky sensor can't take the node offline."""
    flags = 0
    if fire:
        flags |= FLAG_FIRE
    if sensor_error:
        flags |= FLAG_SENSOR_ERROR
    if simulated:
        flags |= FLAG_SIMULATED

    body = _STRUCT.pack(
        VERSION,
        _clamp(int(node_id), 1, 254),
        int(seq) & 0xFF,
        _clamp(int(round(temp_c * 10)), -32768, 32767),
        _clamp(int(round(humidity)), 0, 100),
        _clamp(int(round(smoke_ppm)), 0, 65535),
        _clamp(int(round(battery_pct)), 0, 100),
        flags,
        0,
    )
    return body + bytes([crc8(body)])


class BadPacket(Exception):
    """Raised when bytes off the radio are not a valid reading."""


def decode(raw: bytes) -> dict:
    """Parse a packet. Raises BadPacket on wrong length, bad CRC, or a
    version this build doesn't understand."""
    if len(raw) != PACKET_SIZE:
        raise BadPacket('expected %d bytes, got %d' % (PACKET_SIZE, len(raw)))

    body, received_crc = raw[:-1], raw[-1]
    if crc8(body) != received_crc:
        raise BadPacket('CRC mismatch')

    version, node_id, seq, temp, hum, smoke, batt, flags, _reserved = _STRUCT.unpack(body)

    if version != VERSION:
        raise BadPacket('unsupported protocol version %d' % version)
    if not 1 <= node_id <= 254:
        raise BadPacket('invalid node id %d' % node_id)

    return {
        'node_id': node_id,
        'seq': seq,
        'temp': temp / 10.0,
        'hum': float(hum),
        'smoke': float(smoke),
        'batt': float(batt),
        'fire': bool(flags & FLAG_FIRE),
        'sensor_error': bool(flags & FLAG_SENSOR_ERROR),
        'simulated': bool(flags & FLAG_SIMULATED),
    }
