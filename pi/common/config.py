"""
config.py — settings shared by every Pi in the network.

Six Raspberry Pis:

    addr 0  GATEWAY    internet, SQLite, dashboard, TDMA beacon source
    addr 1  CH-A       cluster head A, in radio range of the gateway
    addr 2  NODE       cluster A member
    addr 3  NODE       cluster A member, backup head for cluster A
    addr 4  CH-B       cluster head B, OUT of gateway range, relays via CH-A
    addr 5  NODE       cluster B member, backup head for cluster B

Data flow:

    2 ─┐
       ├─► CH-A (1) ─────────────────► GATEWAY (0)
    3 ─┘      ▲
              │
    5 ──► CH-B (4)

FREQ, AIR_SPEED, NET_ID, CRYPT_KEY, FRAME_SECONDS and SLOT_MS must be
IDENTICAL on all six Pis. Only NODE_ID differs.
"""

import os

# ---------------------------------------------------------------- radio ----

# Frequency in MHz. The module tunes 850.125-930.125.
#   India   865-867 MHz  -> 866      (licence-exempt)
#   Europe  863-870 MHz  -> 868
# The box says "868M" — that is the hardware band, not a fixed channel.
FREQ = int(os.environ.get('LORA_FREQ', 866))

# 2400 bps is the longest range. Raise only if a slot cannot fit the traffic.
AIR_SPEED = int(os.environ.get('LORA_AIR_SPEED', 2400))

# Transmit power in dBm: 10, 13, 17 or 22.
POWER = int(os.environ.get('LORA_POWER', 22))

NET_ID = int(os.environ.get('LORA_NET_ID', 0))
CRYPT_KEY = int(os.environ.get('LORA_CRYPT', 0x2F1A))

# Pi 3B+/4 hardware UART is ttyS0. Verify with: ls -l /dev/serial*
SERIAL_PORT = os.environ.get('LORA_SERIAL', '/dev/ttyS0')

# ------------------------------------------------------------ addressing ----

GATEWAY_ADDR = 0
BROADCAST_ADDR = 255

NODE_ID = int(os.environ.get('NODE_ID', 1))

ROLE_GATEWAY = 'gateway'
ROLE_HEAD = 'head'
ROLE_NODE = 'node'

# Fixed roles. Deterministic beats elected at this scale: five nodes cannot
# usefully negotiate, and a fixed map means you can site the antennas for the
# links you know you need. Backups are declared so a head failure is covered
# without any election protocol.
ROLES = {
    0: ROLE_GATEWAY,
    1: ROLE_HEAD,     # cluster A
    2: ROLE_NODE,
    3: ROLE_NODE,     # backup head, cluster A
    4: ROLE_HEAD,     # cluster B
    5: ROLE_NODE,     # backup head, cluster B
}

CLUSTER_OF = {1: 'A', 2: 'A', 3: 'A', 4: 'B', 5: 'B'}
HEAD_OF_CLUSTER = {'A': 1, 'B': 4}
BACKUP_HEAD = {'A': 3, 'B': 5}     # promoted if the primary head goes quiet

# Inverse of BACKUP_HEAD: which head each backup is standing by for.
# A promoted backup ADOPTS THE DEAD HEAD'S SLOTS rather than being given its
# own. That is the whole reason failover is collision-free: the primary is
# silent, so its slots are free, and exactly one radio still owns each slot.
BACKUP_OF = {node: HEAD_OF_CLUSTER[cluster]
             for cluster, node in BACKUP_HEAD.items()}

# ------------------------------------------------------------ routing ------
#
# LOCAL MINIMA — why they cannot occur here.
#
# A local minimum is a failure mode of GREEDY GEOGRAPHIC forwarding: a node
# picks whichever neighbour is physically closest to the sink, and gets stuck
# where no neighbour is closer, even when a longer path exists. We do not use
# greedy forwarding. Every node has an explicit, ordered list of next hops
# written below. A packet either goes to a listed hop or is buffered — it can
# never be handed to a neighbour that has no path onward.
#
# The ordered list is also the repair mechanism: if the first hop does not
# acknowledge after ACK_RETRIES, the sender falls through to the next entry.
#
ROUTES = {
    1: [GATEWAY_ADDR],          # CH-A -> gateway
    2: [1, 3],                  # -> CH-A, else the cluster-A backup head
    3: [1, GATEWAY_ADDR],       # -> CH-A, else direct (3 is within gateway range)
    4: [1, GATEWAY_ADDR],       # CH-B -> CH-A, else direct if propagation allows
    5: [4, 1],                  # -> CH-B, else CH-A directly
}

# Members each head expects to hear from, used to size its receive window.
MEMBERS_OF = {1: [2, 3], 4: [5]}

# ------------------------------------------------------------- TDMA --------
#
# COLLISIONS — why they cannot occur here.
#
# Every transmission happens inside a slot owned by exactly one radio. Nothing
# else is permitted to key up in that slot, so two packets can never overlap.
# This is stronger than CSMA: there is no contention window, no exponential
# backoff, and no hidden-terminal problem (CH-B cannot hear node 2, so carrier
# sense would not have protected them from each other anyway).
#
# Slot map, one superframe:
#
#   slot 0   GATEWAY  beacon (broadcast)        — heard by 1, 2, 3
#   slot 1   node 1   CH-A rebroadcasts beacon  — heard by 4
#   slot 2   node 4   CH-B rebroadcasts beacon  — heard by 5
#   slot 3   node 1   CH-A own reading      -> gateway
#   slot 4   node 2   reading               -> CH-A
#   slot 5   node 3   reading               -> CH-A
#   slot 6   node 4   CH-B own reading      -> CH-A
#   slot 7   node 5   reading               -> CH-B
#   slot 8   node 4   CH-B forwards cluster-B buffer -> CH-A
#   slot 9   node 1   CH-A forwards everything       -> gateway
#   ...      all radios asleep until the next frame
#
# Slots 1 and 2 exist because cluster B is deliberately sited outside gateway
# range — that is what makes the relay real. Those nodes therefore cannot hear
# the gateway's beacon either, and an unsynced node drifts until it transmits
# into someone else's slot. Sync is relayed hop by hop along the same tree the
# data travels: gateway -> CH-A -> CH-B -> node 5. Each relay needs its OWN
# slot; two heads rebroadcasting in one slot would collide with each other.
FRAME_SECONDS = int(os.environ.get('FRAME_SECONDS', 60))

# One slot must comfortably exceed worst-case airtime plus an ACK plus guard.
# At 2400 bps a 16-byte packet with the module's 7 bytes of framing is ~77 ms;
# an ACK is the same. 2000 ms leaves room for retries and clock drift.
SLOT_MS = int(os.environ.get('SLOT_MS', 2000))

BEACON_SLOT = 0
DATA_SLOT = {1: 3, 2: 4, 3: 5, 4: 6, 5: 7}      # node id -> its own-reading slot
FORWARD_SLOT = {4: 8, 1: 9}                      # head id -> its relay slot
SLOT_COUNT = 10

# Which transmitter's beacon each node listens to. 0 is the gateway itself;
# any other value is a node that rebroadcasts it.
BEACON_VIA = {1: 0, 2: 0, 3: 0, 4: 1, 5: 4}

# Nodes that rebroadcast the beacon, each in its own slot.
BEACON_RELAY_SLOT = {1: 1, 4: 2}
BEACON_RELAYS = set(BEACON_RELAY_SLOT)

# Guard band at each end of a slot, absorbing clock drift between beacons.
GUARD_MS = int(os.environ.get('GUARD_MS', 120))

# Acknowledgement
ACK_TIMEOUT_MS = int(os.environ.get('ACK_TIMEOUT_MS', 400))
ACK_RETRIES = int(os.environ.get('ACK_RETRIES', 3))

# Free-run this many frames without a beacon before declaring loss of sync.
# The node keeps transmitting on its own clock; slots widen the risk of drift
# but staying silent would be worse.
BEACON_MISS_LIMIT = int(os.environ.get('BEACON_MISS_LIMIT', 5))

# A head that misses this many consecutive frames is presumed down, and its
# cluster's backup head starts accepting traffic.
HEAD_MISS_LIMIT = int(os.environ.get('HEAD_MISS_LIMIT', 3))

# ------------------------------------------------------- remote command ----
#
# HALT auto-expires after this many frames unless the gateway keeps asserting
# it. This is a SAFETY property, not a convenience: a halted fire-detection
# network is a silent fire-detection network, and the failure mode of a
# latched stop is that somebody halts it for a demonstration, forgets, and the
# system is dark the day it matters. With a dead-man timer the worst case is
# 30 minutes of silence rather than indefinite.
#
# Set to 0 to latch HALT until explicitly resumed. Not recommended.
HALT_EXPIRY_FRAMES = int(os.environ.get('HALT_EXPIRY_FRAMES', 30))

# ------------------------------------------------------------- energy ------
#
# Radios sleep outside their own slots. Duty cycle per role, one 60 s frame:
#
#   member   beacon + own slot                    =  2 slots =  4 s  ->  6.7 %
#   CH-B     beacon + own + member 5 + forward    =  4 slots =  8 s  -> 13.3 %
#   CH-A     beacon + own + members 2,3 + rx 6 + tx 7 = 6 slots = 12 s -> 20 %
#
# On a Pi the SoC dominates the radio, so this is about wake time, not TX power.
RADIO_SLEEP_BETWEEN_SLOTS = os.environ.get('RADIO_SLEEP', '1') not in ('0', 'false')

# ----------------------------------------------------------- placement -----

SITE_NAME = os.environ.get('SITE_NAME', 'Bandipur Sector 4')

NODES = {
    1: {'name': 'CH-A — Ridge East',     'lat': 11.6642, 'lng': 76.6242},
    2: {'name': 'Node 2 — Fire Line A',  'lat': 11.6636, 'lng': 76.6311},
    3: {'name': 'Node 3 — Watchtower',   'lat': 11.6621, 'lng': 76.6362},
    4: {'name': 'CH-B — Creek Bed',      'lat': 11.6591, 'lng': 76.6229},
    5: {'name': 'Node 5 — Bamboo Belt',  'lat': 11.6596, 'lng': 76.6284},
}

GATEWAY_POS = {'name': 'Gateway', 'lat': 11.6600, 'lng': 76.6300}

# ------------------------------------------------------------- decision ----

RULES = {
    'temp_high': 45.0,
    'smoke_high': 320.0,
    'hum_low': 25.0,
    'temp_warn': 38.0,
    'smoke_warn': 180.0,
    'batt_low': 20.0,
}

# A confirmed fire jumps the reporting rate. The node keeps its assigned slot —
# it simply uses it every frame instead of every FIRE_FRAME_DIVISOR frames —
# so raising the rate can never cause a collision.
UPLINK_EVERY_FRAMES = int(os.environ.get('UPLINK_EVERY_FRAMES', 1))
FIRE_CONFIRM_CYCLES = int(os.environ.get('FIRE_CONFIRM_CYCLES', 2))

OFFLINE_AFTER = int(os.environ.get('OFFLINE_AFTER', FRAME_SECONDS * 3))

# ------------------------------------------------------------- storage ----

DB_PATH = os.environ.get('DB_PATH', os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'readings.db'))

RETENTION_DAYS = int(os.environ.get('RETENTION_DAYS', 7))


# --------------------------------------------------------------- helpers ---

def role_of(node_id):
    return ROLES.get(node_id, ROLE_NODE)


def is_head(node_id):
    return role_of(node_id) == ROLE_HEAD


def routes_for(node_id):
    """Ordered next hops. First is primary; the rest are repair routes."""
    return ROUTES.get(node_id, [GATEWAY_ADDR])


def evaluate(temp, smoke, hum, batt):
    """Return 'fire' | 'warning' | 'normal' for a set of readings."""
    r = RULES
    if temp >= r['temp_high'] and smoke >= r['smoke_high'] and hum <= r['hum_low']:
        return 'fire'
    if temp >= r['temp_warn'] or smoke >= r['smoke_warn'] or batt <= r['batt_low']:
        return 'warning'
    return 'normal'


def beacon_listen_slot(node_id):
    """The slot in which this node hears its frame sync — the gateway's own
    beacon, or a relayed copy from the head that can reach it."""
    via = BEACON_VIA.get(node_id, GATEWAY_ADDR)
    return BEACON_SLOT if via == GATEWAY_ADDR else BEACON_RELAY_SLOT[via]


def slots_i_listen_in(node_id):
    """Slots this node must have its radio awake for.

    Derived from ROUTES rather than hand-listed, because the two must agree:
    if any node can send to us — as its primary hop OR as its repair route —
    we have to be listening when it does, or that route silently does not
    exist. Listing members by hand is how CH-A ended up asleep during CH-B's
    own data slot.
    """
    listen = {beacon_listen_slot(node_id)}

    # Backups do NOT listen to their head's beacon relay. Promotion is
    # triggered by unacknowledged uplinks instead, which costs no extra
    # airtime and cannot misfire on a head that is alive but out of sync.

    for other in DATA_SLOT:
        if other == node_id:
            continue
        if node_id in routes_for(other):
            listen.add(DATA_SLOT[other])

    for other, slot in FORWARD_SLOT.items():
        if other != node_id and node_id in routes_for(other):
            listen.add(slot)

    return listen


def duty_cycle_of(node_id):
    """Fraction of the frame this node's radio is powered, for reporting."""
    awake = set(slots_i_listen_in(node_id))
    if node_id in DATA_SLOT:
        awake.add(DATA_SLOT[node_id])
    if node_id in FORWARD_SLOT:
        awake.add(FORWARD_SLOT[node_id])
    if node_id in BEACON_RELAY_SLOT:
        awake.add(BEACON_RELAY_SLOT[node_id])
    return (len(awake) * SLOT_MS / 1000.0) / FRAME_SECONDS
