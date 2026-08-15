"""
config.py — settings shared by nodes and gateway.

Every Pi in the network MUST agree on FREQ, AIR_SPEED, NET_ID and CRYPT_KEY
or they simply won't hear each other. Only NODE_ID differs per device.
"""

import os

# ---------------------------------------------------------------- radio ----

# Frequency in MHz. The module is tunable 850.125–930.125.
#
# IMPORTANT — pick the band your country actually licenses:
#   India   865–867 MHz  -> use 866      (866.125 MHz actual)
#   Europe  863–870 MHz  -> use 868
# The box says "868M" but that describes the hardware band, not a fixed
# channel. Running 868 in India puts you outside the licence-exempt
# allocation, so 866 is the safe default here.
FREQ = int(os.environ.get('LORA_FREQ', 866))

# 2400 bps is the longest range. Raise to 9600 only if uplinks collide.
AIR_SPEED = int(os.environ.get('LORA_AIR_SPEED', 2400))

# Transmit power in dBm: 10, 13, 17 or 22.
POWER = int(os.environ.get('LORA_POWER', 22))

# Network id and 16-bit key. Change the key from the default so a neighbouring
# project on the same frequency can't inject readings into your dashboard.
NET_ID = int(os.environ.get('LORA_NET_ID', 0))
CRYPT_KEY = int(os.environ.get('LORA_CRYPT', 0x2F1A))

# Serial device. Pi 3B+/4 hardware UART is ttyS0. Verify with:
#   ls -l /dev/serial*
SERIAL_PORT = os.environ.get('LORA_SERIAL', '/dev/ttyS0')

# ------------------------------------------------------------ addressing ----

GATEWAY_ADDR = 0

# Override per sensor Pi:  export NODE_ID=3
NODE_ID = int(os.environ.get('NODE_ID', 1))

# Human-readable names and map positions, keyed by node id.
# The gateway serves these to the dashboard. Set real GPS coordinates once
# the nodes are physically placed.
NODES = {
    1: {'name': 'Node 1 — Ridge East',  'lat': 11.6642, 'lng': 76.6242},
    2: {'name': 'Node 2 — Fire Line A', 'lat': 11.6636, 'lng': 76.6311},
    3: {'name': 'Node 3 — Watchtower',  'lat': 11.6621, 'lng': 76.6362},
    4: {'name': 'Node 4 — Creek Bed',   'lat': 11.6591, 'lng': 76.6229},
    5: {'name': 'Node 5 — Bamboo Belt', 'lat': 11.6596, 'lng': 76.6284},
}

SITE_NAME = os.environ.get('SITE_NAME', 'Bandipur Sector 4')

# ------------------------------------------------------------- decision ----

# Mirror of the dashboard rules. Nodes evaluate these locally so a fire is
# flagged even if the gateway link is down.
RULES = {
    'temp_high': 45.0,
    'smoke_high': 320.0,
    'hum_low': 25.0,
    'temp_warn': 38.0,
    'smoke_warn': 180.0,
    'batt_low': 20.0,
}

# Seconds between uplinks. 60 s is fine for mains-powered test rigs;
# stretch to 300+ for battery deployments.
UPLINK_INTERVAL = int(os.environ.get('UPLINK_INTERVAL', 60))

# A node is shown offline if nothing arrives for this many seconds.
OFFLINE_AFTER = int(os.environ.get('OFFLINE_AFTER', UPLINK_INTERVAL * 3))

# ------------------------------------------------------------- storage ----

DB_PATH = os.environ.get('DB_PATH', os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'readings.db'))

RETENTION_DAYS = int(os.environ.get('RETENTION_DAYS', 7))


def evaluate(temp, smoke, hum, batt):
    """Return 'fire' | 'warning' | 'normal' for a set of readings."""
    r = RULES
    if temp >= r['temp_high'] and smoke >= r['smoke_high'] and hum <= r['hum_low']:
        return 'fire'
    if temp >= r['temp_warn'] or smoke >= r['smoke_warn'] or batt <= r['batt_low']:
        return 'warning'
    return 'normal'
