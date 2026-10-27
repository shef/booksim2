# TPU 3D torus: Ironwood (TPU7) and TPU 8t

Google's training TPUs since v4 join 64-chip copper cubes into a 3D torus
through optical circuit switches (OCS). Ironwood (2025) is the current
generation; TPU 8t (announced 2026) keeps the same construction at twice the
link rate. Only Ironwood is modelled here; 8t is assumed to share its layout.

## Sources

| Source | Date | What it gives |
|---|---|---|
| Jouppi et al., "TPU v4", ISCA 2023 | 2023 | cubes, OCS count and radix, twisted tori |
| Zu et al., "Resiliency at Scale: Managing Google's TPUv4 Machine Learning Supercomputer", NSDI 2024 | 2024 | dimension-order routing, fault-tolerant routing method, failure rates |
| Google Cloud TPU7x documentation | 2025 | chip, cube and pod facts, ICI bandwidth |
| Ironwood launch blog | Apr 2025 | pod sizes (256 and 9,216 chips) |
| Google cluster reliability blog | May 2026 | availability target: 130 of 144 cubes for 95% of the month |
| Google TPU 8 technical deep dive | 2026 | 8t: 3D torus, 9,600-chip superpod, 2x Ironwood ICI |
| Jouppi and Lakshmanamurthy, Hot Chips 2026 | Aug 2026 | 8t: 6 links x 8 lanes at 1.6 Tb/s per link; superpod drawn as 150 cubes |

## Physical fabric

- A cube is a 4x4x4 block of 64 chips joined by copper: one rack on Ironwood
  (16 trays of 4 chips). Each chip has 6 ICI links (+/-X, +/-Y, +/-Z).
- Each cube face carries 16 links (4x4), so a cube has 96 links that leave
  it. They go through OCSes, which join faces of different cubes and so build
  slices of any shape whose dimensions are multiples of 4, including twisted
  tori.
- An Ironwood pod is 144 cubes (9,216 chips); an 8t superpod is 9,600 chips.

### OCS layout (derived)

On TPU v4, 48 OCSes served 64 cubes. The count follows from the geometry: the
+X link at a given face position of one cube must be able to meet the -X link
at the same position of any other cube, so one switch carries one face
position in one dimension for every cube. That gives 3 dimensions x 16
positions = 48 switches, each with 2 ports per cube (128 for 64 cubes, on a
136-port switch).

Applied to Ironwood: 144 cubes x 2 = 288 ports per switch, 48 switches, and
13,824 duplex fiber pairs between racks and switches. A 300-port switch fits
with 12 spare ports. Switch vendors quote radix as duplex LC ports, which is
what a duplex link end uses.

## Link and latency parameters

| Parameter | Value | Confidence | Basis |
|---|---|---|---|
| Links per chip | 6 | published | TPU7x docs |
| Ironwood link rate per direction | 800 Gb/s | derived | 6 links x 200 GB/s bidirectional (TPU7x docs) |
| 8t link rate per direction | 1.6 Tb/s | published | Hot Chips 2026 |
| Copper hop (inside a cube) | 90 ns | assumed | SerDes, FEC and short copper |
| Fiber hop (through the OCS) | 580 ns | assumed | 80 ns of SerDes and FEC plus 100 m of fiber at 5 ns/m: 50 m from each TPU to the OCS |
| Receive buffer per ICI port | 512 KB (Ironwood), 1 MB (8t) | assumed | covers the credit round trip up to about 250 m from TPU to OCS (see below) |
| Simulator time base | 1 cycle = 1 ns, 1 flit = 200 B | assumed | one flit per cycle is 1.6 Tb/s |

## Model

- One router per chip with 6 link ports; links leaving a cube get the fiber
  latency, links inside it the copper latency.
- Simulated slice: 1,024 chips as 8x8x16, plain and twisted (a wrap in a
  short dimension shifts the long dimension by 8).
- Fault-free routing: dimension-order, longest dimension first, with a
  dateline VC per ring (`dor`, or `src` with the same routes on a twisted
  torus). 2 VCs.
- Buffers: dimension-order routing carries a neighbour hop on a single VC, so
  with buffers split per VC each VC must cover the fiber round trip alone
  (2,048 flits per VC at 50 m). With a shared buffer per port
  (`buffer_policy = shared`, a small private slice per VC) the port total
  counts.
- Fault routes: NSDI 2024's method, dimension-order routing plus at most one
  "wild" first hop per dimension, with the set of paths chosen to minimize the
  busiest link under all-to-all traffic. Written as `src` routes.
- Exporter: [`utils/fabric/torus.py`](../../utils/fabric/torus.py) (topology,
  fault-free and fault routes); all-reduce traffic from
  [`utils/fabric/collectives.py`](../../utils/fabric/collectives.py).

### Buffer size and cable reach (derived)

Credit-based flow control keeps a link at full rate only if the receive
buffer covers one credit round trip. With 80 ns of electronics each way,
5 ns/m of fiber and a TPU-to-OCS distance `d` (fiber path `2d`):

```
RTT  ~ 2 x (80 + 10 d) + 10 = 170 + 20 d   ns
d_max = (B - 170) / 20  m       B in flits of 200 B at 1.6 Tb/s (1 flit/ns)
d_max = (2B - 170) / 20 m       at 800 Gb/s
```

| Buffer per port | Reach at 1.6 Tb/s | Reach at 800 Gb/s |
|---|---|---|
| 100 KB (512 flits) | 17 m | 43 m |
| 410 KB (2,048 flits) | 94 m | 196 m |
| 512 KB (2,621 flits) | 123 m | 250 m |
| 1 MB (5,242 flits) | 250 m | 516 m |

Beyond the reach a link does not fail; its throughput falls to buffer / RTT.
A few hundred KB to 1 MB per port is cheap at current process nodes (6 MB of
SRAM for six ports is a few mm^2 at most), so cable length is more likely set
by latency, optics reach and floor layout than by buffer area. The real
buffer sizes are not published.

## Failure model

| Failure unit | What it removes | Handling |
|---|---|---|
| One OCS | one face position in one dimension, on every cube's pair of faces in the slice | fault-tolerant routes (NSDI method) |
| One cube (chip, tray, power) | the whole cube | replace with a spare cube through the OCS |
| One optical link | one link | fault-tolerant routes, or a spare cube |

One OCS outage is the case simulated. Because each switch serves one face
position, the loss is spread evenly across the slice: 2 of 16 face links if
one failure unit holds two positions, 1 of 16 if it holds one.

## Assumptions

| Assumption | Value | Basis | Confidence | Effect on results |
|---|---|---|---|---|
| Cube | 4x4x4, copper inside | TPU7x docs | published | defines the model |
| Pod | 144 cubes | 9,216 / 64 | derived | pod-level availability only |
| OCS per face position | 1 switch, full pod reach | v4 geometry (ISCA 2023) | derived for v4, assumed for Ironwood | defines the failure unit |
| OCS radix | about 300 duplex ports, 288 used | 288 needed; vendor product class | assumed | none (cost and spares) |
| Positions lost per OCS failure | 1, or 2 if a box holds two | packaging not published | assumed | small: with 2 positions, adjacent and spread placements give the same all-to-all loss (87.1% kept on 4x4x8, Z) |
| Hop latencies | 90 ns copper, 580 ns fiber (50 m to the OCS) | estimate | assumed | latency and buffer sizing; not throughput when buffers cover the round trip |
| Receive buffer | 512 KB per port (Ironwood), 1 MB (8t), shared across VCs | sized for about 250 m of reach at each generation's link rate | assumed | none within reach; beyond it throughput falls as buffer / RTT |
| 8t layout | same as Ironwood at 2x link rate | nothing published beyond pod size | assumed | 8t results are Ironwood results rescaled |
| 8t pod | 150 cubes, 32 chips per rack | Hot Chips 2026 superpod drawing | derived | pod-level availability only |

## Validation

- Mean hop count of dimension-order routing on 8x8x16 matches the analytical
  value (8 links).
- To do: reproduce NSDI 2024's measured throughput under one OCS failure on a
  4x4x4 slice with our fault routes.

## Open questions

- Ironwood's real OCS count and radix, and whether one box holds several
  face positions.
- Whether twisted tori are offered on Ironwood (the documented slice shapes
  allow them).
- 8t's cube packaging now that a cube spans two racks.
