#!/usr/bin/env python3
"""
sensor_node.py — runs on all five non-gateway Pis.

One program, two roles, decided by NODE_ID in config.ROLES:

  ROLE_NODE  reads its sensors and transmits to its cluster head.
  ROLE_HEAD  does that too, and additionally listens for its members,
             acknowledges them, buffers their readings, and forwards the
             whole buffer onward in its own relay slot.

Run:
    NODE_ID=2 SMOKE_MODE=simulate DHT_MODE=simulate python3 sensor_node.py

Everything is slot-timed. The node is awake only in the slots it owns or must
listen to; the radio is put to sleep in between. See pi/common/tdma.py.

The class takes its radio, clock and sensor module as arguments so the whole
stack can be driven by pi/tools/netsim.py against a virtual radio. On real
hardware the defaults are used and nothing is injected.
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


class RealClock:
    def monotonic(self):
        return time.monotonic()

    def sleep_until(self, when):
        delay = when - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class SensorNode:

    def __init__(self, node_id=None, radio=None, clock=None, sensors=None,
                 log=None):
        self.id = node_id if node_id is not None else config.NODE_ID
        self.role = config.role_of(self.id)
        self.cluster = config.CLUSTER_OF.get(self.id)
        self.routes = config.routes_for(self.id)
        self.members = config.MEMBERS_OF.get(self.id, [])

        self.clock = clock or RealClock()
        self.log = log or (lambda msg: print(msg, flush=True))

        if sensors is None:
            import sensors as sensors_mod
            sensors = sensors_mod
        self.sensors = sensors

        if radio is None:
            import radio as radio_mod
            radio = radio_mod.Radio(self.id)
        self.radio = radio

        self.sf = tdma.Superframe(self.id)
        self.seq = 0
        self.previous = None
        self.fire_streak = 0
        self.buffer = []          # store-and-forward, heads only
        # (origin, seq) already accepted. When our ACK is lost the sender
        # retransmits, and without this the head would buffer and relay the
        # same reading several times — wasting airtime and battery on every
        # hop between here and the gateway.
        self._seen = set()

        # Backup-head state. `standby_for` is the head we cover; once promoted
        # we adopt its slots, which are free precisely because it is silent.
        # Remote command state, driven by the beacon.
        self.halted = False
        self.last_cmd_seq = None
        self.halted_frames = 0

        self.standby_for = config.BACKUP_OF.get(self.id)
        self.promoted = False
        self.head_silent_frames = 0
        self.MAX_BUFFER = 64
        self.stats = {
            'sent': 0, 'delivered': 0, 'dropped': 0, 'retries': 0,
            'repairs': 0, 'relayed': 0, 'acked': 0, 'beacons': 0, 'commands': 0,
        }

    # ---- helpers -----------------------------------------------------

    def _name(self):
        return config.NODES.get(self.id, {}).get('name', 'Node %d' % self.id)

    def _ack_matcher(self, origin, seq):
        def match(p):
            return (p.get('type') == protocol.TYPE_ACK and
                    p.get('dst') == self.id and
                    p.get('origin') == origin and
                    p.get('seq') == seq)
        return match

    # ---- transmission with acknowledgement and route repair ----------

    def send_reading(self, reading, window_end):
        """Send one reading along the route, trying each hop in order.

        Returns the hop that acknowledged, or None. Falling through to the
        next entry in ROUTES is the route-repair mechanism: there is no
        greedy choice to make, so a packet can never be handed to a neighbour
        with no path onward — which is exactly why local minima cannot arise.
        """
        for hop_index, hop in enumerate(self.routes):
            for attempt in range(config.ACK_RETRIES):
                if self.clock.monotonic() >= window_end:
                    self.stats['dropped'] += 1
                    return None

                packet = protocol.encode_data(
                    src=self.id, dst=hop,
                    origin=reading['origin'], seq=reading['seq'],
                    temp_c=reading['temp'], humidity=reading['hum'],
                    smoke_ppm=reading['smoke'], battery_pct=reading['batt'],
                    fire=reading['fire'], sensor_error=reading['sensor_error'],
                    simulated=reading['simulated'],
                    relayed=reading['origin'] != self.id,
                    hops=reading['hops'])

                self.radio.send(packet, hop)
                self.stats['sent'] += 1

                deadline = min(window_end,
                               self.clock.monotonic() + config.ACK_TIMEOUT_MS / 1000.0)
                got = self.radio.recv_until(
                    deadline, match=self._ack_matcher(reading['origin'], reading['seq']))

                if got:
                    self.stats['delivered'] += 1
                    if hop_index > 0:
                        self.stats['repairs'] += 1
                        self.log('[%d] route repair: %s unreachable, delivered via %s'
                                 % (self.id, self.routes[0], hop))
                    return hop

                self.stats['retries'] += 1

            self.log('[%d] no ACK from %s after %d attempts'
                     % (self.id, hop, config.ACK_RETRIES))

        self.stats['dropped'] += 1
        return None

    # ---- slot handlers ------------------------------------------------

    def do_beacon_slot(self):
        """Listen for frame sync — from the gateway directly, or relayed by a
        head if we are outside gateway range."""
        slot = config.beacon_listen_slot(self.id)
        _, window_end = self.sf.slot_window(slot)
        expect_from = config.BEACON_VIA.get(self.id, config.GATEWAY_ADDR)

        got = self.radio.recv_until(
            window_end,
            match=lambda p: (p.get('type') == protocol.TYPE_BEACON and
                             p.get('src') == expect_from))

        if got:
            beacon, _rssi = got
            self.sf.on_beacon(beacon['seq'], received_at=self.sf.frame_start)
            self.sf.last_beacon = beacon['seq']
            self.sf.last_command = beacon.get('command', protocol.CMD_NONE)
            self.sf.last_cmd_seq = beacon.get('cmd_seq', 0)
            self.stats['beacons'] += 1
            self.apply_command(beacon.get('command', protocol.CMD_NONE),
                               beacon.get('cmd_seq', 0))
        else:
            self.sf.note_missed_beacon()
            if not self.sf.synced:
                self.log('[%d] no beacon for %d frames — free-running'
                         % (self.id, self.sf.missed_beacons))


    def watch_head(self, head_acked):
        """Backups only: promote after HEAD_MISS_LIMIT frames in which our own
        head failed to acknowledge us.

        The trigger is deliberately "did my head ACK my uplink", NOT "did I
        hear its beacon". A head can be perfectly alive and still relay no
        beacon — if it has itself lost upstream sync it has nothing to
        rebroadcast. Promoting on beacon silence makes a backup seize the
        slots of a working head, and then BOTH transmit in them. That is a
        guaranteed collision, and it is exactly what the simulator caught.

        An ACK failure is unambiguous: the head cannot serve us, so taking
        over is both safe and useful. Promotion adopts the head's slots rather
        than allocating new ones, so the schedule stays collision-free — the
        head we are replacing is, by this test, not talking to us at all.
        """
        if self.standby_for is None or self.promoted:
            return

        if head_acked:
            self.head_silent_frames = 0
            return

        self.head_silent_frames += 1
        if self.head_silent_frames < config.HEAD_MISS_LIMIT:
            return

        head = self.standby_for
        self.promoted = True
        self.role = config.ROLE_HEAD

        if head in config.FORWARD_SLOT:
            self.sf.tx_slots.add(config.FORWARD_SLOT[head])
        if head in config.BEACON_RELAY_SLOT:
            self.sf.tx_slots.add(config.BEACON_RELAY_SLOT[head])
        # Take over listening for everything that routed through the head.
        self.sf.rx_slots |= set(config.slots_i_listen_in(head))
        self.sf.rx_slots.discard(config.FORWARD_SLOT.get(head))
        self.sf.active_slots = self.sf.tx_slots | self.sf.rx_slots

        # Our own traffic must now skip the dead head too.
        self.routes = [h for h in self.routes if h != head] or [config.GATEWAY_ADDR]

        self.log('[%d] PROMOTED to cluster head — %d took over from %d after '
                 '%d silent frames (adopted slots %s)'
                 % (self.id, self.id, head, self.head_silent_frames,
                    sorted(self.sf.tx_slots)))

    def forward_slot_i_own(self):
        """Our own forward slot, or the one we adopted on promotion."""
        if self.id in config.FORWARD_SLOT:
            return config.FORWARD_SLOT[self.id]
        if self.promoted and self.standby_for in config.FORWARD_SLOT:
            return config.FORWARD_SLOT[self.standby_for]
        return None

    def beacon_relay_slot_i_own(self):
        if self.id in config.BEACON_RELAY_SLOT:
            return config.BEACON_RELAY_SLOT[self.id]
        if self.promoted and self.standby_for in config.BEACON_RELAY_SLOT:
            return config.BEACON_RELAY_SLOT[self.standby_for]
        return None

    def apply_command(self, command, cmd_seq):
        """Act on a downlink command carried in the beacon.

        Acted on once per cmd_seq value. The beacon repeats every frame, so
        without that guard RESTART would fire continuously and HALT could not
        be distinguished from a stale repeat.
        """
        if command == protocol.CMD_NONE:
            return
        if cmd_seq == self.last_cmd_seq:
            return                       # already handled this one
        self.last_cmd_seq = cmd_seq

        if command == protocol.CMD_HALT:
            self.halted = True
            self.halted_frames = 0
            self.stats['commands'] += 1
            self.log('[%d] HALT received — transmission stopped, still listening'
                     % self.id)

        elif command == protocol.CMD_RESUME:
            if self.halted:
                self.log('[%d] RESUME received — normal operation' % self.id)
            self.halted = False
            self.halted_frames = 0
            self.stats['commands'] += 1

        elif command == protocol.CMD_RESTART:
            self.log('[%d] RESTART received — clearing state' % self.id)
            self.seq = 0
            self.buffer = []
            self._seen = set()
            self.fire_streak = 0
            self.previous = None
            self.halted = False
            self.halted_frames = 0
            self.head_silent_frames = 0
            if self.promoted:
                # Step back down; the primary head is presumed restarted too.
                self.promoted = False
                self.role = config.role_of(self.id)
                self.sf = tdma.Superframe(self.id)
                self.routes = config.routes_for(self.id)
                self.log('[%d] stepped down from promoted head' % self.id)
            self.stats['commands'] += 1

    def check_halt_expiry(self):
        """Dead-man timer. A halted fire-detection network is a silent one, so
        HALT lapses unless the gateway keeps asserting it."""
        if not self.halted or config.HALT_EXPIRY_FRAMES <= 0:
            return
        self.halted_frames += 1
        if self.halted_frames >= config.HALT_EXPIRY_FRAMES:
            self.halted = False
            self.halted_frames = 0
            self.log('[%d] HALT expired after %d frames — resuming automatically'
                     % (self.id, config.HALT_EXPIRY_FRAMES))

    def do_beacon_relay_slot(self):
        """Rebroadcast the beacon for members that cannot hear the gateway.

        Without this, cluster B never gets frame sync — it is deliberately
        sited out of gateway range — and unsynced nodes drift until their
        transmissions land in someone else's slot.
        """
        frame_number = self.sf.last_beacon
        if frame_number is None:
            return                       # nothing to relay yet
        # Relay the command along with the sync. Without this, cluster B —
        # which only ever hears a relayed beacon — could never be commanded.
        self.radio.send(
            protocol.encode_beacon(src=self.id, frame_number=frame_number,
                                   frame_seconds=config.FRAME_SECONDS,
                                   slots=config.SLOT_COUNT,
                                   command=getattr(self.sf, 'last_command',
                                                   protocol.CMD_NONE),
                                   cmd_seq=getattr(self.sf, 'last_cmd_seq', 0)),
            config.BROADCAST_ADDR)

    def do_own_data_slot(self):
        """Read the sensors and transmit our own measurement."""
        _, window_end = self.sf.slot_window(config.DATA_SLOT[self.id])

        raw, sensor_error = self.sensors.read_all(self.previous)
        self.previous = raw

        status = config.evaluate(raw['temp'], raw['smoke'], raw['hum'], raw['batt'])
        self.fire_streak = self.fire_streak + 1 if status == 'fire' else 0
        fire_confirmed = self.fire_streak >= config.FIRE_CONFIRM_CYCLES

        reading = {
            'origin': self.id, 'seq': self.seq,
            'temp': raw['temp'], 'hum': raw['hum'],
            'smoke': raw['smoke'], 'batt': raw['batt'],
            'fire': fire_confirmed, 'sensor_error': sensor_error,
            'simulated': self.sensors.is_simulated(), 'hops': 0,
        }

        self.log('[%d] seq=%3d %.1fC %.0f%%RH %.0fppm batt %.0f%% %s%s'
                 % (self.id, self.seq, raw['temp'], raw['hum'], raw['smoke'],
                    raw['batt'], status.upper(),
                    '  *** FIRE ***' if fire_confirmed else ''))

        hop = self.send_reading(reading, window_end)

        # Liveness of our cluster head, judged by whether it acknowledged us.
        if self.standby_for is not None:
            self.watch_head(hop == self.standby_for)

        if hop is None:
            # Store and forward. A reading that could not be handed off is
            # held, not discarded — the head may be rebooting, or a backup may
            # be about to promote. Bounded so a long outage cannot exhaust
            # memory; the oldest readings are dropped first because a stale
            # temperature is worth less than a recent one.
            self.buffer.append(reading)
            if len(self.buffer) > self.MAX_BUFFER:
                self.buffer = self.buffer[-self.MAX_BUFFER:]
            self.log('[%d] no route right now — buffered (%d held)'
                     % (self.id, len(self.buffer)))

        self.seq = (self.seq + 1) & 0xFF

        # Drain anything held over, using whatever is left of our own slot.
        # A node without a forward slot has nowhere else to do this, and
        # without it a buffered reading would never leave the node.
        if self.buffer and self.forward_slot_i_own() is None:
            self.drain_buffer(window_end)

    def drain_buffer(self, window_end):
        """Push buffered readings while time remains in the current slot.

        Fire first: if the window runs out, the alert is what has to get
        through. Whatever does not fit stays buffered for the next frame.
        """
        pending = sorted(self.buffer, key=lambda r: not r['fire'])
        self.buffer = []
        sent = 0

        for reading in pending:
            if self.clock.monotonic() >= window_end:
                self.buffer.append(reading)
                continue
            if self.send_reading(reading, window_end) is not None:
                sent += 1
                self.stats['relayed'] += 1
            else:
                self.buffer.append(reading)

        if sent:
            self.log('[%d] drained %d buffered reading(s)%s'
                     % (self.id, sent,
                        ', %d still held' % len(self.buffer) if self.buffer else ''))
        return sent

    def do_listen_slot(self, slot):
        """A head listening in a member's slot, or CH-A listening for CH-B."""
        _, window_end = self.sf.slot_window(slot)

        while self.clock.monotonic() < window_end:
            for packet, rssi in self.radio.poll():
                if packet.get('type') != protocol.TYPE_DATA:
                    continue
                if packet.get('dst') != self.id:
                    continue

                # Acknowledge first — the sender is waiting and its slot is
                # short. Buffering can happen after the ACK is on the air.
                # A repeat is acknowledged again (the sender clearly missed the
                # first ACK) but is not buffered a second time.
                self.radio.send(
                    protocol.encode_ack(self.id, packet['src'],
                                        packet['origin'], packet['seq']),
                    packet['src'])
                self.stats['acked'] += 1

                key = (packet['origin'], packet['seq'])
                if key in self._seen:
                    continue
                self._seen.add(key)
                if len(self._seen) > 512:
                    self._seen = set(list(self._seen)[-128:])

                self.buffer.append({
                    'origin': packet['origin'], 'seq': packet['seq'],
                    'temp': packet['temp'], 'hum': packet['hum'],
                    'smoke': packet['smoke'], 'batt': packet['batt'],
                    'fire': packet['fire'],
                    'sensor_error': packet['sensor_error'],
                    'simulated': packet['simulated'],
                    'hops': packet['hops'] + 1,
                    'rssi': rssi,
                })
                self.log('[%d] rx from %d (origin %d seq %d)%s'
                         % (self.id, packet['src'], packet['origin'],
                            packet['seq'], '  FIRE' if packet['fire'] else ''))
            self.clock.sleep_until(self.clock.monotonic() + 0.005)

    def do_forward_slot(self):
        """A head bursting its buffer onward, in its own dedicated slot.

        Note drain_buffer uses `is not None` rather than a truth test:
        send_reading returns the hop that acknowledged, and the gateway's
        address is 0 — which is falsy. A plain `if` silently re-queues every
        reading that reached the gateway, and the buffer never drains.
        """
        if not self.buffer:
            return
        slot = self.forward_slot_i_own()
        if slot is None:
            return
        _, window_end = self.sf.slot_window(slot)
        self.drain_buffer(window_end)

    # ---- frame loop ----------------------------------------------------

    def run_frame(self):
        """Walk one superframe. The radio is woken only for slots we own or
        must listen to, and slept in between."""
        for slot in range(config.SLOT_COUNT):
            if slot not in self.sf.active_slots:
                continue

            # Wake at the slot boundary, but do not key up until the guard
            # band has passed — every receiver needs that margin to have its
            # radio on, and clocks between beacons are only approximately
            # aligned. Transmitting at the exact boundary loses packets to
            # receivers that have not finished waking.
            self.clock.sleep_until(self.sf.slot_start(slot))
            self.radio.wake()
            self.clock.sleep_until(self.sf.slot_window(slot)[0])

            # While halted we suppress DATA only. Two slots stay active because
            # they are control plane, not payload:
            #
            #   - our beacon LISTEN slot, which is how RESUME reaches us;
            #   - our beacon RELAY slot, if we have one.
            #
            # The relay matters more than it looks. Cluster B hears only a
            # relayed beacon, so a halted CH-A that stopped relaying would cut
            # cluster B off from the command channel entirely: it would never
            # receive the HALT, would keep transmitting, and could never be
            # resumed over the air. Halting must propagate, not partition.
            control_slots = {config.beacon_listen_slot(self.id)}
            relay = self.beacon_relay_slot_i_own()
            if relay is not None:
                control_slots.add(relay)

            if self.halted and slot not in control_slots:
                self.radio.sleep()
                continue

            if slot == config.beacon_listen_slot(self.id):
                self.do_beacon_slot()
            elif slot == self.beacon_relay_slot_i_own():
                self.do_beacon_relay_slot()
            elif slot == config.DATA_SLOT.get(self.id):
                self.do_own_data_slot()
            elif slot == self.forward_slot_i_own():
                self.do_forward_slot()
            else:
                self.do_listen_slot(slot)

            self.radio.sleep()

        self.check_halt_expiry()
        self.clock.sleep_until(self.sf.frame_start + self.sf.frame_seconds)
        self.sf.advance_frame()

    def run(self):
        tdma.assert_schedule_ok()
        self.log('[%d] %s — role=%s cluster=%s routes=%s'
                 % (self.id, self._name(), self.role, self.cluster, self.routes))
        self.log('[%d] %s' % (self.id, self.sf.describe()))
        try:
            while True:
                self.run_frame()
        except KeyboardInterrupt:
            self.log('\n[%d] stopped. %s' % (self.id, self.stats))
        finally:
            self.radio.close()


def main():
    SensorNode().run()


if __name__ == '__main__':
    main()
