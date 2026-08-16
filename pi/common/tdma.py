"""
tdma.py — the superframe clock. This is what makes collisions impossible.

Time is divided into frames of FRAME_SECONDS, and the head of each frame into
SLOT_COUNT slots of SLOT_MS. Every slot is owned by exactly one radio. A node
transmits only inside a slot it owns, so two packets can never overlap.

Why TDMA rather than CSMA here:

  * The node set is fixed and known, so slots can be assigned statically. No
    contention window, no exponential backoff, no wasted listening.
  * CSMA would not have worked anyway. CH-B cannot hear node 2 — they are the
    classic hidden terminal pair. Carrier sense at node 2 says "channel clear"
    while CH-B is mid-transmission, and both step on CH-A.
  * Slots let the radio sleep on a schedule, which is the entire energy story.

Frame alignment comes from the gateway's beacon in slot 0. A node that has
never heard a beacon free-runs on its own clock and still transmits; it just
cannot guarantee alignment, so it is reported as unsynced.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


class Superframe:
    """Tracks where we are in the frame and when our slots come round."""

    def __init__(self, node_id):
        self.node_id = node_id
        self.frame_seconds = config.FRAME_SECONDS
        self.slot_ms = config.SLOT_MS

        # monotonic timestamp of the current frame's slot 0
        self.frame_start = time.monotonic()
        self.frame_number = 0
        self.synced = False
        self.missed_beacons = 0

        self.last_beacon = None       # frame number to rebroadcast, if we relay
        self.last_command = 0         # command to relay onward with it
        self.last_cmd_seq = 0

        self.tx_slots = set()
        if node_id in config.DATA_SLOT:
            self.tx_slots.add(config.DATA_SLOT[node_id])
        if node_id in config.FORWARD_SLOT:
            self.tx_slots.add(config.FORWARD_SLOT[node_id])
        if node_id in config.BEACON_RELAY_SLOT:
            self.tx_slots.add(config.BEACON_RELAY_SLOT[node_id])

        self.rx_slots = set(config.slots_i_listen_in(node_id))
        self.active_slots = self.tx_slots | self.rx_slots

    # ---- sync --------------------------------------------------------

    def on_beacon(self, frame_number, received_at=None):
        """Align to the gateway. Called the moment a beacon decodes."""
        self.frame_start = received_at if received_at is not None else time.monotonic()
        self.frame_number = frame_number
        self.synced = True
        self.missed_beacons = 0

    def note_missed_beacon(self):
        self.missed_beacons += 1
        if self.missed_beacons >= config.BEACON_MISS_LIMIT:
            self.synced = False

    # ---- position ----------------------------------------------------

    def advance_frame(self):
        """Roll to the next frame on our own clock, used when free-running or
        between beacons."""
        self.frame_start += self.frame_seconds
        self.frame_number = (self.frame_number + 1) & 0xFF

    def slot_start(self, slot):
        """Monotonic time this slot opens in the current frame."""
        return self.frame_start + slot * self.slot_ms / 1000.0

    def slot_end(self, slot):
        return self.slot_start(slot) + self.slot_ms / 1000.0

    def slot_window(self, slot):
        """Usable transmit window, guard band removed from both ends."""
        guard = config.GUARD_MS / 1000.0
        return self.slot_start(slot) + guard, self.slot_end(slot) - guard

    def current_slot(self, now=None):
        """Slot index we are in, or None if the frame's active portion is over
        and every radio should be asleep."""
        now = now if now is not None else time.monotonic()
        offset = now - self.frame_start
        if offset < 0:
            return None
        slot = int(offset / (self.slot_ms / 1000.0))
        return slot if slot < config.SLOT_COUNT else None

    def frame_elapsed(self, now=None):
        now = now if now is not None else time.monotonic()
        return now - self.frame_start

    def frame_over(self, now=None):
        return self.frame_elapsed(now) >= self.frame_seconds

    def sleep_until_slot(self, slot, now=None):
        """Seconds to wait before `slot` opens. Negative means it has passed."""
        now = now if now is not None else time.monotonic()
        return self.slot_start(slot) - now

    def next_active_slot(self, now=None):
        """The next slot this node must be awake for, or None if done for
        this frame."""
        now = now if now is not None else time.monotonic()
        for slot in sorted(self.active_slots):
            if self.slot_start(slot) > now:
                return slot
        return None

    def duty_cycle(self):
        return (len(self.active_slots) * self.slot_ms / 1000.0) / self.frame_seconds

    def describe(self):
        return ('node %d  tx=%s rx=%s  duty=%.1f%%  frame=%ds slot=%dms'
                % (self.node_id, sorted(self.tx_slots), sorted(self.rx_slots),
                   100 * self.duty_cycle(), self.frame_seconds, self.slot_ms))


def validate_schedule():
    """Fail loudly if the slot map lets two radios transmit at once, or if a
    slot is too short for the traffic it must carry.

    Called at startup by every role, so a bad edit to config.py is caught on
    the bench rather than in the forest.
    """
    import protocol

    errors = []
    owner = {}

    def claim(slot, who):
        if slot in owner:
            errors.append('slot %d claimed by both %s and %s' % (slot, owner[slot], who))
        else:
            owner[slot] = who

    claim(config.BEACON_SLOT, 'gateway beacon')
    for relay, slot in sorted(config.BEACON_RELAY_SLOT.items()):
        claim(slot, 'node %d beacon relay' % relay)
    for node_id, slot in sorted(config.DATA_SLOT.items()):
        claim(slot, 'node %d data' % node_id)
    for head_id, slot in sorted(config.FORWARD_SLOT.items()):
        claim(slot, 'head %d forward' % head_id)

    # Every node must be able to hear a beacon from somewhere, or it will
    # free-run and eventually drift into another node's slot.
    for node_id in config.NODES:
        via = config.BEACON_VIA.get(node_id)
        if via is None:
            errors.append('node %d has no beacon source' % node_id)
        elif via != config.GATEWAY_ADDR and via not in config.BEACON_RELAYS:
            errors.append('node %d syncs off node %d, which does not relay the beacon'
                          % (node_id, via))

    for slot in owner:
        if slot >= config.SLOT_COUNT:
            errors.append('slot %d is outside SLOT_COUNT=%d' % (slot, config.SLOT_COUNT))

    # A forwarding head may burst its whole cluster in one slot.
    air = protocol.airtime_ms(config.AIR_SPEED)
    worst_burst = max([len(m) + 1 for m in config.MEMBERS_OF.values()] or [1])
    needed = worst_burst * (air + config.ACK_TIMEOUT_MS) + 2 * config.GUARD_MS
    if needed > config.SLOT_MS:
        errors.append('SLOT_MS=%d too short: a %d-packet burst needs ~%dms'
                      % (config.SLOT_MS, worst_burst, needed))

    active = config.SLOT_COUNT * config.SLOT_MS / 1000.0
    if active > config.FRAME_SECONDS:
        errors.append('active slots total %.1fs but FRAME_SECONDS=%d'
                      % (active, config.FRAME_SECONDS))

    # Every node must have a route whose first hop can actually hear it.
    for node_id in config.ROUTES:
        for hop in config.routes_for(node_id):
            if hop != config.GATEWAY_ADDR and hop not in config.ROLES:
                errors.append('node %d routes via unknown node %d' % (node_id, hop))

    return errors


def assert_schedule_ok():
    errors = validate_schedule()
    if errors:
        raise SystemExit('[tdma] invalid schedule in config.py:\n  - ' +
                         '\n  - '.join(errors))
