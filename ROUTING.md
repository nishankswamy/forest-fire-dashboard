# Network Protocol Layer

`routing.js` implements the multi-hop routing that a single-hop star topology
cannot demonstrate. It is pure logic — no DOM, no dependencies — so it can be
unit-tested headlessly, and the dashboard reads it through `data-source.js`.

Four things are implemented:

| Feature | Where |
|---|---|
| Cluster-head election with residual-energy weighting | `electHeads()` |
| Backup cluster head with heartbeat-based promotion | `electBackups()`, `checkHeartbeats()` |
| Routing tables with acknowledged, retried forwarding | `buildTables()`, `transmit()` |
| Greedy geographic forwarding + local-minima recovery | `route()`, `walkFace()` |

---

## Read this first: what is real and what is simulated

**The firmware on the Raspberry Pis does not use any of this.** `pi/` remains a
single-hop star: all five sensor nodes transmit directly to the gateway at LoRa
address 0. This routing layer runs only in the browser simulation.

That was a deliberate choice, and the reasoning matters if you are asked about it:

- With 5 nodes spread over ~3 km and LoRa reaching several km, **every node can
  already reach the gateway directly**. Clustering would add hops, add failure
  modes, and *reduce* delivery. There is no performance case for it at this scale.
- These protocols pay off at tens to hundreds of nodes, where most nodes are out
  of range of the sink. The simulation runs 50 nodes for exactly that reason.
- So: this is a demonstration of the protocols, not an optimisation of your
  deployment. Do not present it as a performance gain on 6 Pis.

---

## 1. Local minima and perimeter recovery

### The problem

Greedy geographic forwarding sends each packet to whichever neighbour is
closest to the destination. It is stateless and cheap. It also fails.

A **local minimum** is a node where no neighbour is closer to the gateway than
the node itself. The packet is stuck, even when a path exists — escaping
requires moving *away* from the destination first, which greedy will never do.

Voids are caused by terrain, water, or dead nodes. This simulation places a
ridge across the site (`OBSTRUCTIONS` in `data-source.js`); LoRa links may not
cross it, which carves a genuine void. Without an obstruction the node field is
uniform, every route is greedy, and there is nothing to show.

### A worked example from the simulation

```
N-03 is 714 m from the gateway. Its neighbours:
    N-11   726 m      N-16   889 m      N-21  1025 m
    N-24  1101 m      N-29  1214 m
```

Every one is *farther* from the gateway. N-03 is a local minimum. The route
that actually works is `N-03 → N-21 → N-08 → GW`, and its first hop goes 311 m
in the wrong direction.

### The recovery

On hitting a local minimum the packet switches to **perimeter mode** and walks
the face of a planar subgraph by the right-hand rule, returning to greedy as
soon as it reaches a node closer to the gateway than the stuck point.

Two details make this work:

**Planarisation.** Perimeter routing is run over the **Gabriel graph**, not the
raw radio graph: edge *(u,v)* is kept only if no other node lies inside the
circle having *uv* as its diameter. On a graph with crossing edges the
right-hand rule can loop forever; planarising guarantees termination. The
Gabriel subgraph here has mean degree 3.9 against the radio graph's 5.1.

**Trying every incident face.** The textbook first choice is the edge
counterclockwise from the direction of the destination. That face is not always
the one that escapes. `route()` therefore tries each face incident to the stuck
node in counterclockwise order, and only reports failure when all are exhausted
— which correctly identifies a genuinely partitioned graph rather than looping.

The recovered route for the example above:

```
N-11 → N-03 → N-11 → N-19 → N-27 → N-14 → N-06 → GW
greedy  perim  perim  perim  perim  perim  greedy
```

### Measured

| Scenario | Delivered | Hit a local minimum | Recovered |
|---|---|---|---|
| No obstruction | 50/50 | 0 | — |
| Ridge across the middle (void) | 50/50 | 11 | **11** |
| Ridge across the whole site (partition) | 37/50 | 13 | 0, correctly reported unreachable |

The third row is the honest case: when the graph is genuinely partitioned no
algorithm can deliver, and the correct behaviour is to say so rather than loop.

---

## 2. Cluster heads, and the backup

Cluster heads aggregate for their members, so *n* members produce one long
transmission instead of *n*. Election is LEACH-style but weighted by residual
energy, with a minimum separation so two heads don't sit on top of each other.

A head burns energy faster than its members — it keeps its radio awake three
times as long to catch their uplinks. So heads are re-elected every 5 rounds
(`CFG.rotateEveryRounds`), moving the cost around the network.

The **backup** is the highest-energy member that can hear its head directly —
it has to notice the head going quiet. After 3 missed heartbeats it promotes
itself, and every member pointing at the dead head is re-homed to it.

### Does rotation actually help?

Set `CFG.rotateEveryRounds = 0` to disable rotation and compare:

| | Rotation ON | Rotation OFF |
|---|---|---|
| First node death | round **307** | round 212 |
| Head-duty spread (sd) | **28.4** | 72.6 |
| Packet delivery ratio | 100.0% | 99.3% |

Rotation delays the first death by **45%** and distributes head duty far more
evenly. Note what it does *not* do: half-network-death lands at round 317 vs
320, essentially unchanged. Rotation equalises lifetime rather than extending
total network life — which is exactly what it is for, and a more interesting
result than "it makes everything better".

---

## 3. Routing tables and acknowledgement

Every node holds an entry: its role, its cluster head, its next hop, hop count,
the full path, and the neighbours it can hear. Members point at their head;
heads route onward to the gateway geographically.

Each hop is acknowledged. A missed ACK is retransmitted up to
`CFG.ackRetries` (3) times before the hop is declared failed. ACKs are lost with
probability `CFG.ackLossRate` (6%), so retries happen constantly — visible as
the *ACK retries* counter climbing on the dashboard while delivery stays at
~100%. That is the point of the mechanism.

---

## 4. Duty cycling

Radios sleep between uplinks: `CFG.dutyWakeMs` awake out of `CFG.dutyPeriodMs`.
Cluster heads stay awake 3× longer than members.

### An important departure from the LEACH literature

Those papers model **motes**, where the radio dominates and a packet costs
microjoules. Ours are **Raspberry Pis**. A Pi 4 draws ~2.1 W awake against
milliwatts of radio, so the SoC dominates and battery life is set almost
entirely by *how long the node stays awake* — not by transmit power or packet
size.

The energy model reflects this: the first-order radio model
(`E_elec = 50 nJ/bit`, `E_amp = 100 pJ/bit/m²`) is kept because it still decides
the *relative* cost of being a cluster head, but the term that actually empties
the battery is `roundSeconds × (duty × activePowerW + (1−duty) × sleepPowerW)`.

The practical consequence, and worth saying out loud in a viva: **a Raspberry Pi
cannot truly sleep like a mote.** The ~0.35 W floor is why battery-powered Pi
sensor nodes are hard, and why real deployments use microcontrollers for the
nodes and reserve a Pi for the gateway.

---

## Demonstrating it

Open the dashboard. The map shows 50 nodes, the gateway (`GW`), and the ridge.

**Cluster structure** — heads are drawn larger with a blue ring, backups with a
dashed border, and faint grey lines connect members to their head.

**Local minima** — click a node with an amber outline. Its route appears on the
map: blue segments are greedy, **amber segments are perimeter mode**. The
routing panel says *"Hit a local minimum; recovered via perimeter routing."*
`N-11` and `N-45` are reliable examples.

**Backup promotion** — select a cluster head, press **Kill node**. Within three
rounds (~9 s) its backup is promoted, the cluster re-homes, and a
`backup promoted` event appears in the protocol log. The *CH promotions*
counter increments.

**Duty cycling** — the routing panel shows each node's radio state and cycle
percentage. Heads read 30%, members 10%. Watch a head's battery fall faster
than its neighbours'.

**Rotation** — leave it running. Head assignments change every 5 rounds and you
can watch the blue rings move around the map.

---

## Configuration

All tunables are in `CFG` at the top of `routing.js`:

| Setting | Default | Effect |
|---|---|---|
| `rangeM` | 620 | Radio range. Raise it and local minima disappear |
| `chFraction` | 0.12 | Share of nodes elected as heads |
| `rotateEveryRounds` | 5 | 0 disables rotation — run the lifetime comparison |
| `ackRetries` | 3 | Transmissions before a hop fails |
| `ackLossRate` | 0.06 | Per-hop ACK loss probability |
| `heartbeatMissesToPromote` | 3 | Rounds before a backup takes over |
| `dutyWakeMs` / `dutyPeriodMs` | 1200 / 12000 | 10% member duty cycle |

`SIM_NODES` in `data-source.js` sets the node count. Set it to 5 to see exactly
what the real hardware would produce — and note that at 5 nodes there are no
local minima, because every node reaches the gateway directly. That is the
whole argument for why this layer lives in the simulator.

---

## Limitations

- **Not implemented in firmware.** See the top of this document.
- **Idealised radio.** Range is a hard 620 m circle. Real LoRa links are
  probabilistic and asymmetric; a link that works one way may not work back,
  which breaks the ACK assumption.
- **Global knowledge.** Election and planarisation are computed centrally.
  A real implementation is distributed: each node knows only its neighbours,
  election needs a negotiation protocol, and nodes disagree during convergence.
- **No mobility.** Nodes are static, which is right for this application but
  means the routing tables never need to converge under churn.
- **Time is abstract.** One round is one 3-second tick representing 60 s of
  wall clock. There is no MAC layer, so no contention or collisions.
