# Forest Fire Detection Using a LoRa Wireless Sensor Network

**Project Report — Draft**

> Written in a standard engineering-report structure so it can be moved into
> the institutional template without rewriting. Section numbering, front matter
> (certificate, declaration, acknowledgement) and formatting will be adapted
> once the template is available.
>
> **Marked throughout:** results labelled *(simulated)* were obtained from the
> software simulator; results labelled *(measured)* come from hardware. At the
> time of drafting, hardware measurements are pending — those placeholders are
> flagged **[TO MEASURE]** so they can be filled in without restructuring.

---

## Abstract

Forest fires cause severe ecological and economic damage, and detection delay
is the dominant factor in how much area burns before a response begins.
Satellite monitoring offers wide coverage but coarse temporal resolution;
ground-based sensing offers immediacy but requires a communication network that
functions without mains power or cellular coverage.

This project implements a ground-based forest fire detection system using a
LoRa wireless sensor network of six Raspberry Pi nodes, together with a
real-time monitoring dashboard. Each sensor node measures temperature,
humidity and smoke concentration, evaluates a three-condition fire rule
locally, and transmits a 16-byte binary packet to a gateway over a
multi-hop LoRa link.

The network is organised into two clusters. Cluster heads aggregate readings
from their members and relay them onward, with one cluster head deliberately
sited outside gateway range so that head-to-head forwarding is exercised.
Two properties are addressed by construction rather than by best effort.
Channel access uses a **TDMA superframe**, in which every transmission occupies
a slot owned by exactly one radio, making collisions structurally impossible.
Routing uses **explicit ordered next-hop tables** rather than greedy geographic
forwarding, so the local-minimum failure mode cannot arise.

Radios sleep outside their assigned slots, giving measured duty cycles of
6.7% for member nodes and 30% for the busiest cluster head. The complete
six-node network was verified in a discrete-event simulator that executes the
actual deployment firmware against a virtual radio channel, recording zero
collisions across 184 transmissions with 100% packet delivery.

A complementary 50-node browser simulator demonstrates LEACH-style cluster-head
election and GPSR greedy-perimeter geographic routing at a scale where those
protocols are appropriate, including recovery from local minima induced by
terrain obstruction.

**Keywords:** wireless sensor networks, LoRa, forest fire detection, TDMA,
multi-hop routing, cluster head, energy efficiency, Raspberry Pi

---

## 1. Introduction

### 1.1 Background

Wildfire detection is fundamentally a race against exponential growth. Fire
spread rate increases with fuel dryness, ambient temperature and wind, and the
area affected grows roughly with the square of elapsed time in the early
phase. Reducing detection latency from hours to minutes therefore has a
disproportionate effect on the eventual size of the event.

Existing approaches fall into three categories:

- **Satellite thermal imaging** (e.g. MODIS, VIIRS) covers large areas but
  revisits a given location only a few times per day, and cloud cover blocks
  observation entirely.
- **Watchtower and camera systems** provide continuous observation but require
  line of sight and human attention, and detect smoke only once a plume is
  large enough to be visible above the canopy.
- **Ground sensor networks** detect the physical precursors — temperature rise,
  humidity collapse, combustion gases — at the point of origin, before a plume
  forms. Their limitation is communication: forest interiors typically lack
  cellular coverage and mains power.

LoRa (Long Range) modulation addresses the communication problem directly. It
is a chirp spread-spectrum technique offering link budgets sufficient for
multi-kilometre range at data rates of a few kbps, at power levels compatible
with battery operation. That trade — very low data rate for very long range —
suits sensor telemetry, where a reading is a handful of bytes.

### 1.2 Problem Statement

Design and implement a ground-based forest fire detection system that:

1. Senses temperature, humidity and smoke at multiple distributed points.
2. Communicates readings to a central gateway without cellular or mains
   infrastructure, including from nodes out of direct gateway range.
3. Avoids packet collisions between nodes sharing a single radio channel.
4. Guarantees that a reading with a valid path to the gateway is not lost to a
   routing dead end.
5. Minimises energy consumption so that battery operation is viable.
6. Presents readings and alerts on a real-time dashboard.

### 1.3 Objectives

| # | Objective |
|---|---|
| O1 | Implement sensing and local fire evaluation on each node |
| O2 | Design a multi-hop LoRa network with cluster heads and relay |
| O3 | Eliminate channel collisions by design, not by retry |
| O4 | Eliminate routing dead ends (local minima) by design |
| O5 | Minimise radio energy via scheduled duty cycling |
| O6 | Provide failover when a cluster head becomes unavailable |
| O7 | Build a dashboard showing live readings, topology and alerts |
| O8 | Verify all of the above before hardware deployment |

### 1.4 Scope and Limitations

**In scope.** A six-node network (one gateway, two cluster heads, three sensor
nodes) over a site of a few square kilometres; a fixed, surveyed topology; a
web dashboard served by the gateway; and a simulator for pre-deployment
verification.

**Out of scope.** Distributed cluster-head election (roles are statically
assigned — see Section 5.3 for justification); node mobility; LoRaWAN network
server integration; automated dispatch or notification to fire services; and
long-term field trials across a fire season.

---

## 2. Literature Survey

### 2.1 Wireless Sensor Networks for Environmental Monitoring

Akyildiz et al. (2002) established the canonical survey framing of wireless
sensor networks, identifying energy as the dominant design constraint and
noting that communication typically dominates computation in the energy
budget. This finding underpins most WSN protocol design, and its applicability
to this project is examined critically in Section 5.5, where it is shown not
to hold for Raspberry Pi class hardware.

### 2.2 Clustering and LEACH

Heinzelman, Chandrakasan and Balakrishnan (2000) introduced **LEACH** (Low
Energy Adaptive Clustering Hierarchy), which organises nodes into clusters
whose heads aggregate member data and transmit onward. The key contributions
are (a) aggregation reduces the number of long transmissions, and (b) the
cluster-head role is *rotated* probabilistically, so that no single node bears
the elevated cost permanently.

LEACH's energy model — the first-order radio model with terms
`E_elec = 50 nJ/bit` and `E_amp = 100 pJ/bit/m²` — has become a standard basis
for comparison. It is adopted for the 50-node simulator in this project, with
an important correction documented in Section 5.5.

Subsequent variants address LEACH's weaknesses: **LEACH-C** centralises
election at the base station using global energy knowledge; **HEED** selects
heads using residual energy and communication cost rather than pure
probability. The election used in this project's simulator is closer to HEED in
spirit, weighting the draw by residual energy and enforcing a minimum spatial
separation between heads.

### 2.3 Geographic Routing and the Local Minimum Problem

Karp and Kung (2000) introduced **GPSR** (Greedy Perimeter Stateless Routing).
Greedy forwarding sends each packet to whichever neighbour lies closest to the
destination, requiring no routing tables — only position knowledge. Its failure
mode is the **local minimum**: a node at which no neighbour is closer to the
destination than the node itself, even though a path exists. Escaping requires
moving *away* from the destination, which greedy forwarding will never do.

GPSR recovers by switching to **perimeter mode**, traversing the faces of a
planar subgraph using the right-hand rule until reaching a node closer to the
destination than the point at which forwarding stalled. Planarisation is
essential: on a graph containing crossing edges the face traversal may fail to
terminate. GPSR uses the **Gabriel graph** (Gabriel and Sokal, 1969), in which
edge *(u,v)* is retained only if no other node lies within the circle having
*uv* as its diameter.

Bose et al. (2001) provide the formal treatment of guaranteed delivery via face
routing on planar subgraphs of unit-disk graphs.

### 2.4 Medium Access Control in Sensor Networks

Channel access divides broadly into contention-based and schedule-based
approaches:

- **CSMA** (carrier sense multiple access) requires each node to listen before
  transmitting. It is simple and requires no coordination, but suffers the
  **hidden terminal problem**: two nodes that cannot hear each other may both
  hear a third, and will transmit simultaneously into it despite both sensing a
  clear channel.
- **TDMA** (time division multiple access) assigns each node a time slot.
  Collisions become impossible by construction, and radios can sleep outside
  their slots. The cost is the need for time synchronisation and a fixed,
  known node set.

The relative merit depends on network size and predictability. This project's
choice, and the specific hidden-terminal pair that motivates it, are analysed
in Section 5.2.

### 2.5 LoRa Physical Layer

LoRa uses chirp spread spectrum, trading data rate for receiver sensitivity.
The SX1262 transceiver used here supports air rates from 2.4 to 62.5 kbps,
transmit power up to 22 dBm, and a manufacturer-quoted range of approximately
5 km in open terrain at the lowest air rate.

An important distinction for this project: **LoRa is not LoRaWAN**. LoRaWAN is
a MAC-layer and network-architecture specification built above LoRa modulation.
The Waveshare SX1262 HAT used here implements a *private point-to-point
protocol* and explicitly does not support LoRaWAN, meaning that network-server
tooling such as The Things Network is inapplicable and the MAC layer must be
designed rather than inherited.

### 2.6 Identified Gap

The surveyed literature evaluates clustering and geographic routing primarily
at simulated scales of tens to hundreds of nodes, where the assumptions —
motes with radio-dominated energy budgets, nodes mostly out of range of the
sink, dense enough neighbourhoods for greedy forwarding to usually succeed —
hold well.

Small physical deployments violate several of these assumptions
simultaneously. This project addresses the resulting design question directly:
which protocol mechanisms are appropriate at six nodes, and which are
appropriate only at scale. The answer, developed in Section 5, is that the two
regimes call for *opposite* techniques, and the project implements both to make
the comparison explicit.

---

## 3. System Requirements

### 3.1 Hardware

| Component | Quantity | Purpose |
|---|---|---|
| Raspberry Pi 4 | 6 | 1 gateway, 2 cluster heads, 3 sensor nodes |
| Waveshare SX1262 868M LoRa HAT (SKU 16806) | 6 | LoRa transceiver, UART interface |
| DHT22 sensor | 5 | Temperature and relative humidity |
| MQ-2 gas sensor | 5 | Smoke and combustible gas |
| MCP3008 ADC | 5 | Analogue-to-digital conversion (Pi has no ADC) |
| SMA antenna | 6 | Supplied with HAT |
| 10 kΩ resistors | 15 | DHT22 pull-up; MQ-2 voltage divider |
| Stacking headers | 5 | GPIO access beneath the HAT |
| microSD cards (32 GB) | 6 | Operating system and storage |

**Critical hardware constraints identified during design:**

1. The Raspberry Pi has **no analogue input**. The MQ-2's analogue output
   cannot be read directly and requires the MCP3008 over SPI.
2. The MQ-2 operates at 5 V and its analogue output can exceed 3.3 V, which
   would damage the MCP3008. A two-resistor divider is required between them.
3. The HAT's mode pins read **HIGH when their jumper caps are removed**. The
   caps must be removed for the Pi to control the module's power mode at all.
4. India's licence-exempt allocation is **865–867 MHz**. Despite the module
   being marketed as "868M", 868 MHz is the European band; the design uses
   866 MHz.

### 3.2 Software

| Layer | Technology |
|---|---|
| Node and gateway firmware | Python 3 |
| Radio driver | Waveshare `sx126x.py` |
| Storage | SQLite (WAL mode) |
| API server | Flask, Flask-CORS |
| Dashboard | HTML5, CSS3, vanilla JavaScript |
| Mapping | Leaflet with Esri satellite imagery |
| Charting | Chart.js |
| Service management | systemd |
| Version control | Git / GitHub |

---

## 4. System Architecture

### 4.1 Network Topology

```
   CLUSTER A                                 CLUSTER B
   ┌────────────┐  ┌────────────┐            ┌────────────┐
   │  node 2    │  │  node 3    │            │  node 5    │
   │  member    │  │  member +  │            │  member +  │
   │            │  │  backup CH │            │  backup CH │
   └─────┬──────┘  └─────┬──────┘            └─────┬──────┘
         │  slot 4       │  slot 5                 │  slot 7
         └───────┬───────┘                         ▼
                 ▼                          ┌─────────────┐
          ┌─────────────┐   slot 8          │  CH-B  (4)  │
          │  CH-A  (1)  │ ◄─────────────────│  cluster    │
          │  cluster    │                   │  head B     │
          │  head A     │                   └─────────────┘
          └──────┬──────┘                   OUTSIDE gateway
                 │ slot 9                   range, by design
                 ▼
        ┌──────────────────┐
        │  GATEWAY  (0)    │  gateway.py → SQLite
        │  beacon, slot 0  │  api.py     → dashboard :5000
        └──────────────────┘
```

Cluster B is deliberately sited beyond gateway range. This is a design
decision, not a limitation: without it, head-to-head forwarding would never be
exercised and the multi-hop capability would be untested. Readings from node 5
travel three hops — `node 5 → CH-B → CH-A → gateway`.

### 4.2 Role Assignment

| Address | Role | Cluster | Primary route | Repair route | Duty cycle |
|---|---|---|---|---|---|
| 0 | Gateway | — | — | — | always on (mains) |
| 1 | Cluster head A | A | gateway | — | 30% |
| 2 | Sensor node | A | CH-A | node 3 | 6.7% |
| 3 | Sensor node, backup CH-A | A | CH-A | gateway | 10% |
| 4 | Cluster head B | B | CH-A | gateway | 16.7% |
| 5 | Sensor node, backup CH-B | B | CH-B | CH-A | 6.7% |

### 4.3 Data Flow

```
Sensor Pi                Gateway Pi                    Browser
─────────                ──────────                    ───────
DHT22 + MQ-2 → MCP3008
   ↓
evaluate fire locally
   ↓
16-byte packet ──LoRa──► gateway.py
                           ↓ CRC-8 check, ACK
                         SQLite (readings.db)
                           ↓
                         api.py :5000 ──HTTP──► dashboard
```

The dashboard is served by the gateway itself, so the API and the web interface
share an origin and no separate hosting is required.

---

## 5. Design Decisions and Justification

This section addresses the decisions that most affect system behaviour. Each
records the alternatives considered and the reasoning, since these are the
points at which the design departs from the surveyed literature.

### 5.1 Packet Format

A binary format was chosen over JSON. At the 2400 bps air rate, a JSON-encoded
reading of approximately 90 bytes occupies roughly 300 ms of airtime; the
16-byte binary packet occupies approximately 77 ms. Shorter airtime reduces
both collision exposure and per-transmission energy.

```
off  size  field     meaning
0    1     version   protocol version (2)
1    1     type      1 BEACON, 2 DATA, 3 ACK
2    1     src       transmitter of this frame
3    1     dst       intended next hop (255 = broadcast)
4    1     origin    node that produced the reading
5    1     seq       sequence number, wraps 0..255
6    2     temp      int16, °C × 10
8    1     hum       uint8, %
9    2     smoke     uint16, ppm
11   1     batt      uint8, %
12   1     flags     fire, sensor error, simulated, relayed
13   1     hops      hops taken so far
14   1     rsv       reserved
15   1     crc       CRC-8 over bytes 0..14
```

The `origin` field is what makes relaying correct: a reading forwarded through
two cluster heads still identifies the node that actually sensed it, while
`src` identifies the immediate transmitter. Without separating these, the
gateway would attribute node 5's measurement to CH-A.

CRC-8 (Dallas/Maxim, polynomial 0x31) is applied above LoRa's own forward error
correction to catch residual bit errors.

### 5.2 Channel Access: TDMA over CSMA

**Decision: TDMA superframe with beacon synchronisation.**

The determining factor is the hidden terminal problem. In this topology, node 2
and CH-B are mutually out of range but both are heard by CH-A. Under CSMA, node
2 performs carrier sense, detects a clear channel because it cannot hear CH-B's
ongoing transmission, and transmits — colliding at CH-A. Carrier sense cannot
prevent this, because the information required is unavailable at the sensing
node.

TDMA removes the problem entirely: node 2 and CH-B transmit in different slots,
so the question of whether they can hear each other never arises.

Three secondary advantages follow:

1. **Deterministic energy.** A node knows exactly when it must be awake, so the
   radio can be scheduled off rather than left listening.
2. **No contention overhead.** No backoff windows, no wasted carrier sensing.
3. **Verifiability.** A slot map is a static object that can be checked for
   correctness before deployment; contention behaviour can only be sampled
   statistically.

The cost is time synchronisation, addressed in Section 5.4.

**Superframe structure** — 60 seconds, ten 2-second slots, then silence:

```
slot 0  gateway  beacon broadcast          heard by 1, 2, 3
slot 1  CH-A     rebroadcasts the beacon   heard by 4
slot 2  CH-B     rebroadcasts the beacon   heard by 5
slot 3  CH-A     own reading    → gateway
slot 4  node 2   reading        → CH-A
slot 5  node 3   reading        → CH-A
slot 6  CH-B     own reading    → CH-A
slot 7  node 5   reading        → CH-B
slot 8  CH-B     forwards cluster B  → CH-A
slot 9  CH-A     forwards everything → gateway
        ── 40 s idle, all radios asleep ──
```

Slot duration is set at 2000 ms against a worst-case requirement of
approximately 77 ms airtime plus a 400 ms acknowledgement window plus retries
and guard bands, giving substantial margin for clock drift.

### 5.3 Routing: Explicit Tables over Greedy Geographic

**Decision: ordered next-hop tables with fallback.**

The local-minimum problem is a property of *greedy geographic forwarding*
specifically. It arises because the forwarding decision is made from local
geometry with no knowledge of whether the chosen neighbour has a path onward.

An explicit routing table cannot produce a local minimum, because there is no
geometric decision to make. Each node holds an ordered list of next hops:

```python
ROUTES = {
    1: [GATEWAY],        # CH-A → gateway
    2: [1, 3],           # → CH-A, else the cluster-A backup head
    3: [1, GATEWAY],     # → CH-A, else direct
    4: [1, GATEWAY],     # CH-B → CH-A, else direct if propagation allows
    5: [4, 1],           # → CH-B, else CH-A
}
```

The ordering doubles as the repair mechanism. If the first hop does not
acknowledge within `ACK_RETRIES` attempts, the sender falls through to the
next. If no listed hop responds, the reading is buffered rather than discarded
— store-and-forward — and retried in the next frame.

**Why not distributed election?** With five nodes, an election protocol costs
airtime and introduces convergence states in which nodes disagree about who the
head is, for no compensating benefit: the topology is fixed and surveyed, and
antenna siting is chosen for the specific links required. Static assignment is
verifiable in advance and cannot diverge. Election becomes worthwhile when node
count, mobility or failure rate makes manual assignment impractical — which is
the regime the 50-node simulator addresses.

### 5.4 Time Synchronisation

TDMA requires a common time reference. The gateway transmits a beacon in slot 0
of every frame, and nodes align their frame clock to its arrival.

A complication emerges directly from the topology: cluster B is out of gateway
range by design, and therefore cannot hear the beacon either. An unsynchronised
node free-runs on its own clock and will eventually drift into another node's
slot — reintroducing exactly the collisions TDMA was chosen to prevent.

The solution is to relay synchronisation along the same tree the data travels:

```
gateway ──beacon──► CH-A ──rebroadcast──► CH-B ──rebroadcast──► node 5
 slot 0              slot 1               slot 2
```

Each relay requires its **own** slot. Two heads rebroadcasting in a shared slot
would collide with each other — the failure the scheme exists to prevent.

### 5.5 Energy Model: A Correction to the Standard Approach

The LEACH first-order radio model was applied initially. Simulating 400
protocol rounds produced **zero node deaths**, indicating the model was
inapplicable.

The reason is a hardware mismatch. The first-order model was formulated for
*motes* — microcontroller-class devices where the radio dominates the energy
budget and a packet costs microjoules. A Raspberry Pi 4 draws approximately
2.1 W while awake, against milliwatts for the radio. Per transmission, the
radio terms amount to roughly 2.4 mJ against a 12 kJ battery: depletion would
require millions of rounds.

The energy model was therefore extended with a platform baseline term:

```
E_round = T_round × (duty × P_active + (1 − duty) × P_sleep)
```

with `P_active = 2.1 W` and `P_sleep = 0.35 W`.

This yields a finding with direct design consequences. On this class of
hardware, **battery life is determined almost entirely by how long the node
stays awake**, not by transmit power or packet size. Optimising transmit energy
on a Raspberry Pi node is close to pointless; duty cycling is the whole story.

A second, harder consequence: the ~0.35 W sleep floor is irreducible, because a
Pi cannot enter the microamp sleep states a microcontroller can. This is the
principal argument for using microcontrollers at the sensor nodes and reserving
a Pi for the gateway in any production deployment — a recommendation carried
into Section 9.

### 5.6 Radio Power Management

The SX1262 HAT's mode is selected by two pins:

| M0 | M1 | Mode | Current |
|---|---|---|---|
| 0 | 0 | Transmission (normal TX/RX) | 100 mA TX / 11 mA RX |
| 0 | 1 | Configuration | — |
| 1 | 0 | WOR (wake on radio) | — |
| 1 | 1 | **Deep sleep** | **2 µA** |

The firmware drives both pins HIGH between slots and both LOW to wake, waiting
100 ms after each change — the settle time the manufacturer's own driver uses.
An insufficient settle time causes the first frame after waking to be
transmitted while the module is still switching mode, presenting as
intermittent, hard-to-diagnose packet loss.

Because silently losing the power saving would invalidate every duty-cycle
figure in this report while leaving the system apparently functional, the
firmware emits an explicit warning if it cannot control these pins.

### 5.7 Fire Decision Rule

```
FIRE     temp ≥ 45 °C  AND  smoke ≥ 320 ppm  AND  humidity ≤ 25 %
WARNING  temp ≥ 38 °C  OR   smoke ≥ 180 ppm  OR   battery ≤ 20 %
```

Requiring all three conditions conjunctively for FIRE is what suppresses false
positives. A hot afternoon raises temperature alone; a passing vehicle raises
smoke alone; neither satisfies the conjunction.

A confirmed fire additionally requires the condition to persist for
`FIRE_CONFIRM_CYCLES = 2` consecutive readings. This debounce costs up to 120
seconds of detection latency — the dominant term in end-to-end latency — and is
a deliberate trade of speed for reliability, on the reasoning that a false
dispatch is expensive and a two-minute delay is small relative to fire growth
timescales.

The decision is evaluated **on the node**, so a node still knows it is
observing a fire when the gateway link is unavailable.

---

## 6. Implementation

### 6.1 Software Structure

```
pi/
├── common/
│   ├── config.py       roles, clusters, routes, slot map — single source of truth
│   ├── protocol.py     packet encode/decode, CRC-8
│   ├── radio.py        SX1262 wrapper with sleep/wake
│   └── tdma.py         superframe clock, schedule validation
├── node/
│   ├── sensor_node.py  role-aware node firmware
│   └── sensors.py      DHT22, MQ-2, MCP3008 drivers
├── gateway/
│   ├── gateway.py      beacon source, receiver, storage
│   ├── db.py           SQLite schema and queries
│   └── api.py          Flask API, serves the dashboard
├── tools/
│   ├── netsim.py       six-node network simulator
│   └── linktest.py     point-to-point link scoring
├── systemd/            three service units
└── setup.sh            one-command provisioning
```

Approximately 2,650 lines of Python, 1,780 lines of JavaScript.

### 6.2 Node Firmware

A single program serves both roles, determined by `NODE_ID`:

- **Sensor node:** reads sensors, evaluates the fire rule, transmits to its
  cluster head, buffers on failure.
- **Cluster head:** additionally listens in its members' slots, acknowledges
  and buffers their readings, and forwards the buffer onward in its own slot.

Each frame walks the slot sequence, waking the radio only for slots the node
owns or must listen to. Transmission begins after the guard band, not at the
slot boundary, so that receivers waking on the same boundary are listening
before the frame goes out.

Duplicate suppression is applied at every hop: when an acknowledgement is lost
the sender retransmits, and without suppression a head would buffer and relay
the same reading repeatedly, wasting airtime on every subsequent hop.

### 6.3 Cluster Head Failover

A backup head monitors whether its primary acknowledges *its own* uplinks. After
`HEAD_MISS_LIMIT` consecutive frames without acknowledgement, the backup
promotes itself, adopts the failed head's slots, and re-homes the cluster.

The trigger is deliberately acknowledgement failure rather than beacon silence.
A head can be operational yet relay no beacon — if it has itself lost upstream
synchronisation it has nothing to rebroadcast. Promoting on beacon silence
causes a backup to seize the slots of a working head, after which **both**
transmit in them. This was implemented, tested, and found to produce 48
collisions before the trigger was corrected (Section 7.4).

Adopting the failed head's slots rather than allocating new ones is what keeps
failover collision-free: the head being replaced is, by the promotion test,
not transmitting.

### 6.4 Gateway

The gateway transmits the synchronisation beacon, receives data addressed to
it, acknowledges, and writes to SQLite in WAL mode so that the API can read
concurrently. Duplicate `(origin, seq)` pairs are acknowledged again — the
sender evidently missed the first acknowledgement — but stored only once.

### 6.5 Dashboard

The dashboard presents a satellite map with status-coloured node markers,
cluster membership and multi-hop route overlays, a per-node detail panel
including its routing table, seven-day history charts, and a fire alert banner.

A single `window.DataSource` interface abstracts the data origin, so the same
rendering code serves both the simulator and live gateway data. Switching
between them requires changing one script tag.

### 6.6 Deployment

`setup.sh` provisions a Pi in one command, installing dependencies, fetching
the radio driver, enabling UART and SPI, and installing systemd units. It also
performs two steps whose omission causes failures that present as radio faults:
disabling the serial login console and removing `console=serial0` from the
kernel command line, both of which otherwise contend for `/dev/ttyS0`.

Before installing any service, it validates the TDMA schedule and refuses to
proceed if two radios could transmit in the same slot.

---

## 7. Testing and Results

### 7.1 Methodology

Verification used three levels:

1. **Unit level** — packet round-trip, CRC rejection of corrupted frames,
   sequence-wrap arithmetic, schedule validation.
2. **Network level** — `netsim.py`, which executes the **actual deployment
   firmware** against a virtual radio channel. It imports `gateway.py` and
   `sensor_node.py` rather than reimplementing them, so what is tested is what
   is deployed.
3. **Integration level** — API endpoints against a seeded database; dashboard
   rendering against captured API responses, in a headless DOM.

The virtual channel models airtime, range (which node can hear which), and
overlap. Any two transmissions overlapping in time that a common neighbour
could hear are recorded as a collision and both frames are discarded.

### 7.2 Collision-Free Operation *(simulated)*

Eight frames, six nodes:

| Metric | Result |
|---|---|
| Transmissions | 184 |
| **Collisions** | **0** |
| Readings delivered | 40 of 40 |
| Duplicates at gateway | 0 |
| Retries | 0 |
| ACK failures | 0 |

### 7.3 Multi-Hop Delivery *(simulated)*

| Node | Route | Hops | Delivered |
|---|---|---|---|
| CH-A (1) | 1 → GW | 1 | 8/8 |
| Node 2 | 2 → 1 → GW | 2 | 8/8 |
| Node 3 | 3 → 1 → GW | 2 | 8/8 |
| CH-B (4) | 4 → 1 → GW | 2 | 8/8 |
| Node 5 | 5 → 4 → 1 → GW | 3 | 8/8 |

Node 5's three-hop path confirms cluster-head-to-cluster-head relay operating
as designed.

### 7.4 Fault Injection *(simulated)*

**Fire at node 5** (the most distant node): the alert propagated across all
three hops and was correctly flagged at the gateway, with zero collisions.

**Cluster head A failure:** node 3 detected the failure after three
unacknowledged frames, promoted itself, adopted CH-A's slots (1, 5 and 9), and
resumed delivery for cluster A. Node 2's route repaired to node 3; node 3's
repaired to direct gateway transmission. **Zero collisions throughout the
failover.**

Cluster B became physically partitioned, as CH-B can hear only CH-A and node 5.
Readings were buffered rather than lost — the correct behaviour, since no
routing algorithm can traverse a physical partition.

### 7.5 Energy *(simulated)*

| Node | Role | Design duty cycle |
|---|---|---|
| 1 | CH-A | 30.0% |
| 2 | Member | 6.7% |
| 3 | Member + backup | 10.0% |
| 4 | CH-B | 16.7% |
| 5 | Member + backup | 6.7% |

Measured awake time in simulation was consistently *below* the design figure,
since nodes complete slot work early and sleep for the remainder.

### 7.6 Cluster-Head Rotation *(50-node simulator)*

| | Rotation enabled | Rotation disabled |
|---|---|---|
| First node death | round **307** | round 212 |
| Head-duty distribution (σ) | **28.4** | 72.6 |
| Packet delivery ratio | 100.0% | 99.3% |

Rotation delays the first node death by **45%** and distributes cluster-head
load far more evenly. Notably, half-network death occurs at round 317 versus
320 — essentially unchanged. **Rotation equalises node lifetime rather than
extending total network lifetime**, which is precisely its intended function
and a more informative result than a uniform improvement would have been.

### 7.7 Local Minima *(50-node simulator)*

A terrain ridge blocking LoRa links was introduced to create voids:

| Scenario | Delivered | Reached a minimum | Recovered |
|---|---|---|---|
| No obstruction | 50/50 | 0 | — |
| Ridge across part of the site | 50/50 | 11 | **11** |
| Ridge across the entire site | 37/50 | 13 | 0 — correctly reported unreachable |

Worked example. Node `N-03` lies 714 m from the gateway; all five of its
neighbours lie farther (726, 889, 1025, 1101 and 1214 m). Greedy forwarding
stalls. The functioning route, `N-03 → N-21 → N-08 → GW`, begins with a hop
311 m *away* from the destination. Perimeter recovery found it.

The third row is significant: when the graph is genuinely partitioned, no
algorithm can deliver, and the correct behaviour is to report unreachability
rather than loop.

### 7.8 Defects Identified by Simulation

The network simulator identified four defects that code review had not:

| # | Defect | Consequence |
|---|---|---|
| 1 | Truth test on a returned hop address | The gateway's address is 0, which is falsy in Python. Every successful delivery to the gateway was read as a failure, so cluster heads re-queued delivered readings indefinitely — 65 duplicates against 19 unique readings |
| 2 | No beacon relay | Cluster B, out of gateway range by design, could never synchronise |
| 3 | Listen slots hand-listed rather than derived from routes | CH-A slept through CH-B's transmission slot; CH-B's own readings had nowhere to land |
| 4 | Failover triggered by beacon silence | A backup seized the slots of a functioning head, producing 48 collisions |

Defects 1 and 3 would have presented on hardware as intermittent, difficult
data loss. Defect 4 would have manifested only under head failure — precisely
the condition the mechanism exists to handle.

### 7.9 API and Dashboard Verification

All five API endpoints return correct responses against a seeded database. The
dashboard was verified in a headless DOM against captured API responses:
node 5's three-hop path renders correctly with per-hop colouring, the routing
panel populates from live fields, and the fire alert triggers.

### 7.10 Hardware Measurements

**[TO MEASURE]** — pending deployment:

| Measurement | Method |
|---|---|
| Link RSSI per hop | `linktest.py` at each mounting position |
| Packet delivery ratio over 24 h | Gateway database row counts |
| Actual detection latency | Timestamped controlled ignition |
| Battery duration per role | Current logging over one week |
| Effective range per link | Field survey |
| Clock drift between beacons | Slot boundary logging |

---

## 8. Conclusion

A ground-based forest fire detection system was designed, implemented and
verified, comprising six Raspberry Pi nodes communicating over LoRa in a
two-cluster multi-hop topology, with a real-time monitoring dashboard.

Against the stated objectives:

| Objective | Outcome |
|---|---|
| O1 Sensing and local evaluation | Achieved — three-condition rule with two-cycle debounce |
| O2 Multi-hop with cluster heads | Achieved — 3-hop relay verified |
| O3 Collision elimination | Achieved — TDMA, 0 collisions in 184 transmissions |
| O4 Routing dead-end elimination | Achieved — explicit tables with ordered fallback |
| O5 Energy minimisation | Achieved in design — 6.7% member duty cycle |
| O6 Cluster head failover | Achieved — promotion verified, collision-free |
| O7 Dashboard | Achieved — live and simulated sources |
| O8 Pre-deployment verification | Achieved — simulator found four defects |

The principal engineering contribution is not any individual mechanism, all of
which exist in the literature, but the demonstration that **protocol choice is
scale-dependent, and that the appropriate choices at six nodes are the opposite
of those at fifty**. Contention-based access and greedy geographic routing are
correct at scale and actively harmful at small deployment size; scheduled
access and static routing are correct at small size and unscalable. Building
and measuring both regimes makes the trade explicit rather than assumed.

A secondary contribution is methodological. Executing the actual deployment
firmware against a simulated channel — rather than a separate model of it —
identified four defects before any hardware was connected, two of which would
have presented as intermittent faults that are extremely difficult to diagnose
in the field.

---

## 9. Future Scope

**Microcontroller sensor nodes.** The energy analysis in Section 5.5 shows the
Raspberry Pi's ~0.35 W sleep floor dominates the budget. ESP32 or STM32 nodes
reaching microamp sleep would extend battery life by orders of magnitude, with
the Pi retained only as gateway.

**Solar harvesting.** With microcontroller nodes, a small panel and cell would
make the deployment indefinitely self-sufficient.

**Adaptive frame length.** The frame is fixed at 60 seconds. Lengthening it in
stable conditions and shortening it under elevated risk would trade latency
against energy dynamically.

**Distributed election.** Beyond roughly twenty nodes, static role assignment
becomes impractical and a negotiated protocol becomes necessary.

**Sensor fusion.** Correlating readings across neighbouring nodes would allow
tighter thresholds without increasing false positives, since a genuine fire
affects multiple nodes in a spatial pattern that noise does not.

**Alert integration.** SMS or push notification to forest department personnel,
rather than requiring a dashboard to be watched.

**Extended field validation.** A full fire season with controlled ignition
tests would establish real detection latency and false-positive rate, neither
of which can be obtained from simulation.

---

## 10. References

> **Note:** verify each against your institution's citation style and confirm
> page numbers and DOIs from the original sources before submission.

1. Akyildiz, I. F., Su, W., Sankarasubramaniam, Y., and Cayirci, E., "Wireless
   sensor networks: a survey," *Computer Networks*, vol. 38, no. 4, 2002.

2. Heinzelman, W. R., Chandrakasan, A., and Balakrishnan, H.,
   "Energy-Efficient Communication Protocol for Wireless Microsensor Networks,"
   *Proceedings of the 33rd Hawaii International Conference on System Sciences
   (HICSS)*, 2000.

3. Karp, B., and Kung, H. T., "GPSR: Greedy Perimeter Stateless Routing for
   Wireless Networks," *Proceedings of the 6th Annual International Conference
   on Mobile Computing and Networking (MobiCom)*, 2000.

4. Gabriel, K. R., and Sokal, R. R., "A new statistical approach to geographic
   variation analysis," *Systematic Zoology*, vol. 18, no. 3, 1969.

5. Bose, P., Morin, P., Stojmenović, I., and Urrutia, J., "Routing with
   guaranteed delivery in ad hoc wireless networks," *Wireless Networks*,
   vol. 7, no. 6, 2001.

6. Younis, O., and Fahmy, S., "HEED: A Hybrid, Energy-Efficient, Distributed
   Clustering Approach for Ad Hoc Sensor Networks," *IEEE Transactions on
   Mobile Computing*, vol. 3, no. 4, 2004.

7. Semtech Corporation, *SX1261/2 Long Range, Low Power, sub-GHz RF
   Transceiver — Datasheet*.

8. Waveshare Electronics, *SX1262 868M LoRa HAT — Wiki and User Manual*.
   Available: https://www.waveshare.com/wiki/SX1262_868M_LoRa_HAT

9. Aosong Electronics, *DHT22 (AM2302) Digital Temperature and Humidity Sensor
   — Datasheet*.

10. Microchip Technology, *MCP3004/3008 8-Channel 10-Bit A/D Converters with
    SPI Serial Interface — Datasheet*.

---

## Appendices

**Appendix A — Source code.** https://github.com/nishankswamy/forest-fire-dashboard

**Appendix B — Wiring diagram.** `pi/docs/wiring.svg`

**Appendix C — Deployment guide.** `pi/README.md`

**Appendix D — Protocol layer documentation.** `ROUTING.md`

**Appendix E — Development log.** `DEVLOG.md` — decisions, defects and
rationale recorded during development.
