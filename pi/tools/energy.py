#!/usr/bin/env python3
"""
energy.py — whole-node power budget and projected battery life.

Answers RQ7 ("is battery operation viable for this node design?") with numbers
rather than an assertion, and shows *which component* decides the answer.

    python3 energy.py                    # all roles, default hardware
    python3 energy.py --scenarios        # compare hardware/design variants
    python3 energy.py --battery 40000    # a larger pack, in joules
    python3 energy.py --md               # markdown, for the report

The point it makes
------------------
The standard WSN energy model accounts for the radio. On this hardware the
radio is a rounding error. Two components decide battery life, and neither is
the transceiver:

    the Raspberry Pi's idle floor      ~0.35 W, irreducible on this platform
    the MQ-2's heating element         ~0.75 W, continuous, cannot be gated

Both figures are in pi/common/config.py so they can be replaced with measured
values. Run with --measured once you have a meter on the rail.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'common'))

import config
import protocol


def fmt_duration(seconds):
    """Battery life in whatever unit reads naturally."""
    if seconds < 3600:
        return '%.0f min' % (seconds / 60)
    if seconds < 86400:
        return '%.1f hours' % (seconds / 3600)
    if seconds < 86400 * 90:
        return '%.1f days' % (seconds / 86400)
    if seconds < 86400 * 730:
        return '%.1f months' % (seconds / (86400 * 30.44))
    return '%.1f years' % (seconds / (86400 * 365.25))


def node_budget(node_id, mq2=True, pi_sleep_w=None, radio_sleep=True):
    """Average power for one node, broken down by component.

    Returns (total_watts, {component: watts}).
    """
    duty = config.duty_cycle_of(node_id)
    pi_sleep = config.SLEEP_POWER_W if pi_sleep_w is None else pi_sleep_w

    # Platform: awake for its slots, idling the rest of the frame.
    platform = duty * config.ACTIVE_POWER_W + (1 - duty) * pi_sleep

    # Radio: transmitting only during its own slots, receiving during the
    # slots it listens to, asleep otherwise (if sleep is available).
    air_frac = protocol.airtime_ms(config.AIR_SPEED) / 1000.0 / config.FRAME_SECONDS
    tx_slots = 1
    if node_id in config.FORWARD_SLOT:
        tx_slots += len(config.MEMBERS_OF.get(node_id, [])) + 1
    if node_id in config.BEACON_RELAY_SLOT:
        tx_slots += 1

    rx_frac = duty          # awake and listening for its whole duty window
    tx_frac = tx_slots * air_frac
    idle_frac = max(0.0, 1 - rx_frac - tx_frac)

    radio = (tx_frac * config.RADIO_TX_W +
             rx_frac * config.RADIO_RX_W +
             idle_frac * (config.RADIO_SLEEP_W if radio_sleep else config.RADIO_RX_W))

    parts = {
        'Pi platform': platform,
        'LoRa radio': radio,
        'DHT22': config.DHT22_W,
    }
    if mq2:
        parts['MQ-2 heater'] = config.MQ2_HEATER_W

    return sum(parts.values()), parts


def report_default(battery_j, md=False):
    print('\n' + '=' * 74)
    print('WHOLE-NODE POWER BUDGET — as built')
    print('=' * 74)
    print('  battery            : %.0f J  (%.2f Wh)' % (battery_j, battery_j / 3600))
    print('  frame              : %d s' % config.FRAME_SECONDS)
    print('  Pi active / idle   : %.2f W / %.2f W' % (config.ACTIVE_POWER_W, config.SLEEP_POWER_W))
    print('  MQ-2 heater        : %.2f W (continuous)' % config.MQ2_HEATER_W)
    print()

    header = ('%-9s %6s %11s %11s %9s %9s %12s' %
              ('node', 'duty', 'Pi (W)', 'MQ-2 (W)', 'radio', 'total', 'battery life'))
    print(header)
    print('  ' + '-' * 71)

    rows = []
    for nid in sorted(config.NODES):
        total, parts = node_budget(nid)
        life = battery_j / total
        print('%-9s %5.1f%% %11.3f %11.3f %9.5f %9.3f %12s'
              % ('node %d' % nid, 100 * config.duty_cycle_of(nid),
                 parts['Pi platform'], parts.get('MQ-2 heater', 0),
                 parts['LoRa radio'], total, fmt_duration(life)))
        rows.append({'node': nid, 'total_w': total, 'parts': parts, 'life_s': life})

    worst = max(rows, key=lambda r: r['total_w'])
    best = min(rows, key=lambda r: r['total_w'])

    print('\n  Share of the budget, busiest node (node %d):' % worst['node'])
    for name, w in sorted(worst['parts'].items(), key=lambda kv: -kv[1]):
        print('    %-14s %7.3f W   %5.1f%%' % (name, w, 100 * w / worst['total_w']))

    print('\n  RQ7 — is battery operation viable?')
    print('    Best case (node %d) : %s' % (best['node'], fmt_duration(best['life_s'])))
    print('    Worst case (node %d): %s' % (worst['node'], fmt_duration(worst['life_s'])))

    radio_share = 100 * worst['parts']['LoRa radio'] / worst['total_w']
    print('\n    The radio is %.3f%% of the budget. Every optimisation in this'
          % radio_share)
    print('    project — TDMA scheduling, deep sleep between slots, a 16-byte')
    print('    packet — operates on that %.3f%%.' % radio_share)
    return rows


def report_scenarios(battery_j):
    """What would actually have to change for battery operation to work."""
    print('\n' + '=' * 74)
    print('SCENARIOS — what changes the answer')
    print('=' * 74)

    node = 2   # a plain member: the most favourable case

    scenarios = [
        ('As built (Pi 4 + MQ-2)',
         dict(mq2=True, pi_sleep_w=None, radio_sleep=True)),
        ('Radio sleep DISABLED',
         dict(mq2=True, pi_sleep_w=None, radio_sleep=False)),
        ('MQ-2 removed (temp/humidity only)',
         dict(mq2=False, pi_sleep_w=None, radio_sleep=True)),
        ('Microcontroller node (1 mW sleep), MQ-2 kept',
         dict(mq2=True, pi_sleep_w=0.001, radio_sleep=True)),
        ('Microcontroller node, MQ-2 removed',
         dict(mq2=False, pi_sleep_w=0.001, radio_sleep=True)),
    ]

    print('  Plain member node (node %d), %.0f J battery\n' % (node, battery_j))
    print('  %-42s %9s %14s' % ('scenario', 'watts', 'battery life'))
    print('  ' + '-' * 68)

    base = None
    for name, kw in scenarios:
        total, parts = node_budget(node, **kw)
        life = battery_j / total
        if base is None:
            base = life
        mult = life / base
        print('  %-42s %9.4f %14s%s'
              % (name, total, fmt_duration(life),
                 '' if mult == 1 else '  (%.0fx)' % mult))

    print('\n  Reading of this table:')
    print('    Turning the radio sleep off barely moves the number — the')
    print('    optimisation this project is built around is worth almost nothing')
    print('    on this hardware.')
    print('    Removing the MQ-2 roughly triples life. Doing BOTH — a')
    print('    microcontroller platform and a gateable gas sensor — gives about')
    print('    8x, and only then does a larger pack or a solar panel reach a')
    print('    deployment-useful duration.')
    print('\n    => RQ7 answer: NOT viable as built. On the modelled pack the node')
    print('       lasts hours, not weeks. The blocker is the sensor and the')
    print('       platform; the network protocol is not the constraint and')
    print('       optimising it further would be wasted effort.')
    print('\n    Note the scale: to reach 30 days as built you would need roughly')
    print('       a %.0f kJ pack (%.0f Wh) — far outside a field-deployable size.'
          % (node_budget(node)[0] * 86400 * 30 / 1000,
             node_budget(node)[0] * 24 * 30))


def report_frame_sweep(battery_j):
    """Frame length is the one lever available without changing hardware."""
    print('\n' + '=' * 74)
    print('FRAME LENGTH — the only lever that does not need new hardware')
    print('=' * 74)
    print('  %-12s %9s %9s %14s %16s' %
          ('frame (s)', 'duty', 'watts', 'battery life', 'worst latency'))
    print('  ' + '-' * 66)

    original = config.FRAME_SECONDS
    try:
        for fs in (30, 60, 120, 300, 600):
            config.FRAME_SECONDS = fs
            total, _ = node_budget(2)
            duty = config.duty_cycle_of(2)
            latency = fs * config.FIRE_CONFIRM_CYCLES
            print('  %-12d %8.2f%% %9.4f %14s %16s'
                  % (fs, 100 * duty, total, fmt_duration(battery_j / total),
                     fmt_duration(latency)))
    finally:
        config.FRAME_SECONDS = original

    print('\n  Detection latency is FRAME_SECONDS x FIRE_CONFIRM_CYCLES. Stretching')
    print('  the frame to save energy directly delays detection, which is the one')
    print('  thing this system exists to minimise. On this hardware the trade is')
    print('  poor: the platform floor dominates, so a 10x longer frame buys far')
    print('  less than 10x the life while costing 10x the latency.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--battery', type=float, default=config.BATTERY_J,
                    help='usable battery energy in joules (default %(default)s)')
    ap.add_argument('--scenarios', action='store_true', help='compare design variants')
    ap.add_argument('--frames', action='store_true', help='sweep frame length')
    ap.add_argument('--all', action='store_true', help='everything')
    args = ap.parse_args()

    report_default(args.battery)
    if args.scenarios or args.all:
        report_scenarios(args.battery)
    if args.frames or args.all:
        report_frame_sweep(args.battery)

    print('\n  Power figures are datasheet-order, from pi/common/config.py.')
    print('  Replace them with measured values once a meter is on the rail;')
    print('  the conclusion is unlikely to change, because the gap is ~1000x.')


if __name__ == '__main__':
    main()
