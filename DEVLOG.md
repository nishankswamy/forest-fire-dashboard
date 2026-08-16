# Development Log

A record of the working session that took this project from a pushed-but-unbuilt
repo to a six-Pi network with collision-free scheduling and multi-hop routing.
Written as a decision log: what was asked, what was decided, what broke, and why
each fix is what it is.

---

## 1. Getting the repo onto GitHub

**Starting state.** The folder was already a git repo — two commits, working tree
clean, `origin` configured to `https://github.com/nishankswamy/forest-fire-dashboard.git`.
No `remotes/origin/main` ref existed, so nothing had ever pushed successfully.

**Findings before pushing.** A secret scan across all 18 tracked files came back
clean; the only match was a placeholder `NNSXS.YOUR-API-KEY` in `README.md`, and
`API_BASE` in `data-source.live.js` was an empty string rather than a hardcoded
address. Safe to make public.

**Snags hit, in order:**

| Problem | Cause | Resolution |
|---|---|---|
| GitHub connector "missing" | The plugin's MCP servers register lazily; the first listing was incomplete | It appeared later as `plugin:engineering:github`, still unauthorized |
| `zsh: command not found: gh` | GitHub CLI doesn't ship with macOS | `brew install gh` |
| `gh repo create --source=.` would fail | A remote named `origin` already existed | Created the empty repo, then `git push -u origin main` against the existing remote |

**Visibility decision: public.** GitHub Pages only serves private repos on a paid
plan, and `.github/workflows/deploy.yml` publishes the dashboard to Pages. Private
would have meant a permanently red workflow.

Pages also needed enabling before the workflow could succeed:

```bash
gh api -X POST repos/nishankswamy/forest-fire-dashboard/pages -f build_type=workflow
```

---

## 2. README cleanup

Removed a stale "Push to GitHub" section still telling the reader to `git init`
and `git remote add origin https://github.com/<your-username>/...`.

Expanded "Run locally" into three parts: the run commands, a numbered
click-through that exercises each layer in turn, and how to switch to live data.
Folded in the CDN caveat — Leaflet, Chart.js and the Esri tiles all load from the
network, so offline gives a working UI with a blank grey map. Without that note a
blank map reads as a bug in the code.

Also fixed the file listing, which omitted `data-source.live.js` and the entire
`pi/` tree.

**Contradiction flagged, not silently fixed.** `README.md` line 3 called this a
"LoRaWAN sensor network"; `pi/README.md` opens by stating in bold that the
Waveshare SX1262 HAT is *not* LoRaWAN — it uses a private point-to-point
protocol. Both cannot be true of the hardware in hand.

---

## 3. Simulator scaled to 50 nodes

**Decision: simulator 50, hardware 5.** `N-01`–`N-05` were kept byte-identical to
`pi/common/config.py` — same ids, names and coordinates — so the real Pis occupy
the same map positions whether the dashboard is on simulated or live data. The
other 45 are generated on a golden-angle spiral, which gives even coverage with
no clustering and is deterministic, so the map is identical on every reload.

Setting `SIM_NODES = 5` collapses it cleanly to exactly what the live gateway
serves.

**Three bugs that only appeared at scale:**

1. **Battery gradient.** Starting charge was `96 - idx * 1.4`. At 9 nodes,
   invisible. At 50, it drew a visible charge gradient across the map and pushed
   the tail into false low-battery warnings. Now cycled, with every 13th node
   deliberately low so the amber state is actually represented.
2. **Fire spread radius.** 850 m against ~390 m mean spacing lit up half the map
   instead of a front. Narrowed to 750 m.
3. **Scroll reset.** `renderList()` rebuilt the `<ul>` every 3 seconds. Invisible
   at 9 nodes; at 50 the list scrolls and the user was yanked back to the top
   mid-scroll. Now preserves `scrollTop`.

---

## 4. Deployment tooling

Four gaps closed before touching six Pis:

**`pi/setup.sh`** — one command per Pi. Installs role-appropriate dependencies,
fetches Waveshare's `sx126x.py` and places it correctly, enables UART and SPI,
adds the user to `dialout`, installs and enables the systemd units. Idempotent.

Two things it does that the prose instructions never mentioned: it disables
`serial-getty@ttyS0` and strips `console=serial0` from the kernel cmdline. The
serial login console holds `/dev/ttyS0` and fights the HAT for it — a classic
"no packets received" cause that presents as a radio fault.

**`pi/systemd/`** — three real unit files templated with `@REPO_DIR@`,
`@RUN_USER@` and `@NODE_ID@`, rather than prose to retype.

> Fixed during review: `StartLimitIntervalSec` and `StartLimitBurst` were in
> `[Service]`. On systemd ≥ 229 those are `[Unit]` keys and are silently ignored
> in `[Service]`.

**`pi/tools/linktest.py`** — point-to-point link scoring, tested against five
synthetic cases including a 250→255→0 sequence wrap that naive subtraction would
score as catastrophic loss.

**`pi/docs/wiring.svg`** — rendered to PNG and visually checked. The first pass
had overflowing footnotes, a clipped MCP3008 box, and an 8-digit hex alpha the
renderer ignored.

`.gitignore` was also expanded — it had no Python rules and nothing preventing
`readings.db` from being committed. Field telemetry doesn't belong in git.

---

## 5. Protocol layer in the simulator

Four features from the project brief, implemented in `routing.js` (browser only)
and documented in `ROUTING.md`.

### Local minima and perimeter recovery

Greedy geographic forwarding sends each packet to whichever neighbour is closest
to the gateway. A **local minimum** is a node where no neighbour is closer — the
packet is stuck even though a path exists, because escaping requires moving
*away* from the destination first.

A worked example from the simulator:

```
N-03 is 714 m from the gateway. Its neighbours:
    N-11  726 m   N-16  889 m   N-21 1025 m   N-24 1101 m   N-29 1214 m
```

Every one is farther. The working route is `N-03 → N-21 → N-08 → GW`, whose
first hop goes 311 m in the wrong direction.

Recovery walks the faces of a **Gabriel-planarised** graph by the right-hand
rule. Planarising matters: on a graph with crossing edges the walk can loop
forever.

**Two bugs, layered.** Perimeter routing first looped 41 hops around one face —
no face-change logic. Once added, the face change reset the loop guard *before*
the termination check compared against it, so every face change read as a
completed traversal. Then a third issue: the textbook first-edge choice doesn't
always escape, so the implementation now tries each incident face in turn and
only reports failure when all are exhausted.

| Scenario | Delivered | Hit a minimum | Recovered |
|---|---|---|---|
| No obstruction | 50/50 | 0 | — |
| Ridge across the middle | 50/50 | 11 | **11** |
| Ridge across the whole site | 37/50 | 13 | 0, correctly reported unreachable |

The third row is the honest case: a partitioned graph cannot be routed, and
saying so beats looping.

### Cluster heads, rotation, and the energy model

**The LEACH energy model gave zero deaths in 400 rounds.** It models motes at
microjoules per packet; a Pi 4 draws ~2.1 W awake and the SoC dominates entirely.
Added a baseline platform term. The honest headline: *a Raspberry Pi cannot sleep
like a mote*, which is why real deployments use microcontrollers for nodes and
reserve a Pi for the gateway.

Rotation on vs off, measured:

| | Rotation ON | Rotation OFF |
|---|---|---|
| First node death | round **307** | round 212 |
| Head-duty spread (sd) | **28.4** | 72.6 |
| Packet delivery ratio | 100.0% | 99.3% |

Rotation delays first death by **45%** and distributes head duty far more evenly.
Note what it does *not* do: half-network death is 317 vs 320, essentially
unchanged. **Rotation equalises lifetime rather than extending it** — which is
exactly its purpose, and a more interesting result than "it helps".

---

## 6. Firmware rewrite for six Pis

### The audit that triggered it

Tracing a fire at node 2 end to end through the real files exposed that of three
stated requirements, only one held:

- **Local minima** — not an issue, but only because there was no multi-hop at
  all. Safe by construction, not by algorithm.
- **Collision** — *not handled anywhere*. No CSMA, no backoff, no TDMA. Worse,
  `time.sleep(max(1.0, interval - elapsed))` had no randomisation, so five Pis
  booting together after a power cut would transmit in lockstep and collide on
  every cycle, forever, with no ACKs to detect it.
- **Energy** — partial. Efficient packets, but the radio never slept.

### Architecture chosen

**2 cluster heads + 3 nodes.** CH-B sits outside gateway range deliberately, so
`node 5 → CH-B → CH-A → gateway` is a genuine 3-hop relay.

**Collisions: TDMA, not CSMA.** With a fixed known node set, slots make overlap
structurally impossible. CSMA would have failed regardless — node 2 cannot hear
CH-B, making them a textbook hidden-terminal pair, so carrier sense at node 2
reports a clear channel while CH-B is mid-transmission.

**Local minima: eliminated by construction.** Minima are a property of *greedy
geographic* forwarding. Explicit ordered next-hop lists cannot produce one. GPSR
belongs in the 50-node simulator, not on 5 Pis.

One 60 s superframe, ten 2 s slots, then every radio sleeps:

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
        ── 40 s of silence, radios off ──
```

Duty cycles: members 6.7%, node 3 10%, CH-B 16.7%, CH-A 30%.

### New and rewritten files

| File | Role |
|---|---|
| `pi/common/config.py` | Roles, clusters, ordered routes, slot map — single source of truth |
| `pi/common/protocol.py` | v2: BEACON/DATA/ACK, origin + hop count, 16 bytes |
| `pi/common/radio.py` | Radio wrapper with sleep/wake, swappable for testing |
| `pi/common/tdma.py` | Superframe clock and `validate_schedule()` |
| `pi/node/sensor_node.py` | Role-aware node: slotted TX, ACK + retry, route repair, store-and-forward |
| `pi/gateway/gateway.py` | Beacon source + receiver + SQLite |
| `pi/tools/netsim.py` | Runs all six real stacks against a virtual radio |

### Four bugs the simulator caught

`netsim.py` imports the *real* `gateway.py` and `sensor_node.py` rather than
reimplementing them, so what it tests is what ships. It found:

1. **Falsy zero.** `if self.send_reading(...)` — the gateway's address *is* 0, so
   every successful delivery to it read as failure. CH-A re-queued delivered
   readings forever: 65 duplicates against 19 unique.
2. **Cluster B could never sync.** Out of gateway range by design means out of
   *beacon* range too. Needed beacon relay slots — one per head, since two heads
   rebroadcasting in one slot collide with each other.
3. **CH-A slept through CH-B's data slot.** Listen slots were hand-listed from
   `MEMBERS_OF` instead of derived from `ROUTES`. Now computed from routes so the
   two cannot disagree.
4. **Backup promotion misfired on a live head.** Triggering on beacon silence
   made node 5 seize CH-B's slots while CH-B was working — 48 collisions. The
   trigger is now unacknowledged uplinks, which is unambiguous: a head can be
   alive and simply have nothing to rebroadcast.

A modelling bug in the harness itself also had to be fixed first — packets were
delivered at transmit *start*, so a head ACKed while the sender was still on air,
registering as a false collision.

### Verified results

```
transmissions 184 · collisions 0 · rows 40 · duplicates 0 · retries 0
node 1 hops=1 · nodes 2,3,4 hops=2 · node 5 hops=3
collisions        : PASS (0)
all nodes reached : PASS
```

Scenarios covered: normal operation, fire at the deepest node, CH-A killed
(backup promotes, cluster B correctly partitions and buffers), and 15% packet
loss.

---

## Two things to know before wiring

**CH-B must be sited where it cannot reach the gateway.** Verify with
`linktest.py`. If it can reach the gateway directly, the relay never exercises
and the multi-hop design is decorative.

**Killing CH-A partitions cluster B entirely**, since CH-B can only hear CH-A and
node 5. Readings buffer rather than deliver. That is correct store-and-forward
behaviour, not a fault — no algorithm can route across a physical partition.

---

## Standing caveat

The 50-node routing layer in `routing.js` is **simulation only**. The firmware in
`pi/` is a separate implementation. At 5 nodes all within gateway range, LEACH-style
clustering would *reduce* delivery rather than improve it — these protocols pay off
at tens to hundreds of nodes. Present the simulator as a protocol demonstration,
not as an optimisation of the deployment.

---

## Commit history

```
84dbf83  Rewrite firmware: TDMA slotting, 2-cluster multi-hop, ACK + route repair, radio duty cycling
e651a43  Add routing layer: clustering, backup CH, ACK, local-minima recovery, duty cycling
1736007  Add provisioning script, systemd units, link test, wiring diagram
b3bd047  Scale simulator to 50 nodes; keep hardware roster at 5
6a14080  Expand run instructions; fix file listing
b22f6ef  Remove stale push-to-GitHub instructions from README
b36dd6e  Forest fire detection dashboard + Pi LoRa stack
90b5f99  Forest fire detection dashboard
```
