"""
radio.py — one wrapper around the Waveshare SX1262 HAT, shared by every role.

Two reasons this exists rather than each script talking to sx126x directly:

  1. Power. The HAT's M0/M1 pins select the module mode, and the driver drives
     them from BCM22/27. Putting the module in its low-power mode between TDMA
     slots is where the radio energy saving actually comes from. sleep()/wake()
     below are the only place that happens, so it cannot be forgotten.

  2. Testability. The whole firmware stack can be run against a virtual radio
     (see pi/tools/netsim.py) by swapping this class out, which is how the
     collision-free claim is verified without six Pis on a bench.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import protocol


class Radio:
    """Real hardware radio."""

    def __init__(self, addr, verbose=True):
        self.addr = addr
        self.buffer = bytearray()
        self.awake = True
        self._module = None
        self._frame_len = 3 + protocol.PACKET_SIZE + 1   # hdr + packet + rssi

        try:
            import sx126x
        except ImportError:
            if verbose:
                print('[radio] sx126x.py not found — DRY RUN, nothing will be '
                      'transmitted. Run pi/setup.sh to install the driver.',
                      flush=True)
            return

        self._module = sx126x.sx126x(
            serial_num=config.SERIAL_PORT,
            freq=config.FREQ,
            addr=addr,
            power=config.POWER,
            rssi=True,
            air_speed=config.AIR_SPEED,
            net_id=config.NET_ID,
            crypt=config.CRYPT_KEY,
        )
        if verbose:
            print('[radio] up: addr=%d freq=%d MHz air=%d bps duty=%.1f%%'
                  % (addr, config.FREQ, config.AIR_SPEED,
                     100 * config.duty_cycle_of(addr)), flush=True)

    # ---- power ------------------------------------------------------

    def sleep(self):
        """Drop the module into low-power mode between slots."""
        if self._module is None or not self.awake:
            return
        self.awake = False
        if not config.RADIO_SLEEP_BETWEEN_SLOTS:
            self.awake = True
            return
        try:
            # M0=1, M1=1 is the module's sleep/configuration mode.
            import RPi.GPIO as GPIO
            GPIO.output(self._module.M0, GPIO.HIGH)
            GPIO.output(self._module.M1, GPIO.HIGH)
        except Exception:
            # No GPIO (dev laptop) or a driver that manages the pins itself —
            # not fatal, we simply do not get the power saving.
            self.awake = True

    def wake(self):
        if self._module is None or self.awake:
            return
        self.awake = True
        try:
            import RPi.GPIO as GPIO
            GPIO.output(self._module.M0, GPIO.LOW)
            GPIO.output(self._module.M1, GPIO.LOW)
            time.sleep(0.01)          # module needs a moment to settle
        except Exception:
            pass

    # ---- transmit / receive -----------------------------------------

    def send(self, packet, dst):
        """Transmit one packet to `dst`. Adds the Waveshare address header."""
        if self._module is None:
            return False
        offset = config.FREQ - 850
        header = bytes([
            (dst >> 8) & 0xFF, dst & 0xFF, offset,
            (self.addr >> 8) & 0xFF, self.addr & 0xFF, offset,
        ])
        self.wake()
        self._module.send(header + packet)
        return True

    def poll(self):
        """Return a list of (decoded, rssi) received since the last call.

        A corrupt frame is discarded one byte at a time until the CRC lines up
        again, which resynchronises without losing frames queued behind it.
        """
        if self._module is None:
            return []

        waiting = self._module.ser.inWaiting()
        if waiting:
            self.buffer.extend(self._module.ser.read(waiting))

        out = []
        while len(self.buffer) >= self._frame_len:
            frame = bytes(self.buffer[:self._frame_len])
            payload = frame[3:3 + protocol.PACKET_SIZE]
            rssi = -(256 - frame[-1])

            try:
                decoded = protocol.decode(payload)
            except protocol.BadPacket:
                del self.buffer[0]
                continue

            del self.buffer[:self._frame_len]
            out.append((decoded, rssi))

        if len(self.buffer) > 4 * self._frame_len:
            del self.buffer[:-self._frame_len]
        return out

    def recv_until(self, deadline, match=None):
        """Block until `deadline` (monotonic seconds), returning the first
        packet that satisfies `match`, or None. Used to wait for an ACK."""
        while time.monotonic() < deadline:
            for decoded, rssi in self.poll():
                if match is None or match(decoded):
                    return decoded, rssi
            time.sleep(0.005)
        return None

    def close(self):
        self.sleep()
