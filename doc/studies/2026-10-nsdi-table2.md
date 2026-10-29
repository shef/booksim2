# NSDI 2024 Table 2: all-to-all under one OCS failure

Date: 2026-10. Fork commit: see the commit that adds this page.
Systems: TPU v4-style 3D torus ([systems/tpu-torus.md](../systems/tpu-torus.md)).

## Question

Do this fork's torus model and fault-tolerant routes reproduce the
all-to-all throughput that Zu et al. (NSDI 2024, Table 2) measured on TPU v4
slices, fault-free and with one OCS unavailable?

## What NSDI reports

Measured all-to-all throughput, with routes chosen by an integer linear
program (ILP) that maximizes all-to-all throughput, one path per pair:

| Slice | No faults | 1 OCS down | Kept |
|---|---|---|---|
| 4x4x4 | 75.9 GB/s | 70.0 GB/s | 92.2% |
| 4x4x8 twisted | 62.1 GB/s | 63.2 GB/s | 101.7% |
| 4x8x8 twisted | 54.3 GB/s | 53.7 GB/s | 98.8% |

The paper gives the ideal for the regular torus as 15/16 (one of 16 face
links lost), and explains the twisted 4x4x8 gain by tiebreaking: balancing
across tiebreak paths shifts traffic between dimensions. It does not say in
which dimension the OCS failed.

Absolute GB/s are not comparable (v4 links, not this fork's), so we compare
the **kept** column and the fault-free ratios between shapes (0.82 and 0.72
of the 4x4x4).

## Setup

- Slices from [`utils/fabric/torus.py`](../../utils/fabric/torus.py): 4x4x4
  copper cubes joined through OCSes, fiber hops at 100 m from TPU to OCS.
- Fault-free routes: dimension-order with the parity tiebreak (`none`), and
  with `--balance` (`bal`): among the shortest dimension-order paths, the set
  that minimizes the busiest link under all-to-all, standing in for NSDI's
  ILP tiebreaking. Both are deadlock-free with 2 dateline VCs.
- One OCS unavailable (`--ocs-fault x|y|z --scenario one`): one face position
  lost in that dimension on every cube crossing. Wild-first routes (NSDI's
  method) chosen by rip-up and reroute to minimize the busiest link, 3 VCs
  (provably deadlock-free; see tpu-torus.md).
- Two measures:
  - **Bound**: (chips − 1) / the most paths on any link, in links per chip.
    This is the all-to-all rate when every pair gets the same bytes over its
    one path, the quantity NSDI's ILP maximizes.
  - **BookSim**: uniform traffic from 6 terminals per chip, offered load swept
    from 0.55 to 1.08 of the balanced bound; saturation is the highest
    accepted rate. 1 MB shared buffer per port, internal speedup 2.

Reproduce (bounds in a few minutes; the sweep about an hour on 16 cores):

```
make -C src
python3 doc/studies/2026-10-nsdi-table2/run.py /tmp/nsdi
```

Raw results: [bounds.csv](2026-10-nsdi-table2/bounds.csv),
[sweep.csv](2026-10-nsdi-table2/sweep.csv).

## Results

All-to-all throughput in links per chip (fraction of a chip's 6 links kept
busy, times 6). "Kept" is one OCS down divided by the balanced fault-free
value.

| Slice | Routes | Bound | BookSim | Kept (bound) | NSDI kept |
|---|---|---|---|---|---|
| 4x4x4 | fault-free, parity | 1.969 | 1.994 | | |
| | fault-free, balanced | 1.969 | 1.989 | | |
| | 1 OCS down (X, Y, Z) | 1.853 | 1.955 * | **94.1%** | 92.2% |
| 4x4x8 twisted | fault-free, parity | 1.221 | 1.306 * | | |
| | fault-free, balanced | 1.693 | 1.716 | | |
| | 1 OCS down (X, Y, Z) | 1.693 | 1.682-1.684 | **100.0%** | 101.7% |
| 4x8x8 twisted | fault-free, parity | 1.250 | 1.270 | | |
| | fault-free, balanced | 1.335 | 1.330 | | |
| | 1 OCS down (X / Y, Z) | 1.364 / 1.371 | 1.341-1.345 | **102.2-102.7%** | 98.8% |

Fault-free ratio to the 4x4x4 (balanced bound): 4x4x8 twisted **0.86**
(NSDI 0.82), 4x8x8 twisted **0.68** (NSDI 0.72).

BookSim agrees with the bound to about 1% except where marked *: there
uniform traffic does not force equal bytes per pair, so pairs that avoid the
busiest link keep flowing while its backlog sits in the 1 MB buffers, and the
measured rate depends on offered load and run length (1.88-1.96 for 4x4x4
with a fault). The bound is the number to compare with NSDI. About 1.3% of
uniform traffic stays on its chip (5 of 383 destination terminals), which is
why fault-free BookSim sits slightly above the bound.

## Findings

- **The model reproduces NSDI's pattern.** On the plain 4x4x4 one OCS
  failure costs about a sixteenth (94.1% against 92.2% measured and NSDI's
  ideal of 15/16 = 93.75%); on the twisted slices it costs nothing to within
  a few percent. Fault-free, the twisted slices lose all-to-all throughput
  relative to 4x4x4 by about NSDI's proportions (0.86 and 0.68 against 0.82
  and 0.72).
- **The twisted "gain" is about the baseline.** With the parity tiebreak,
  twisted fault-free throughput is much lower (1.22 against 1.69 on 4x4x8),
  and the optimized fault routes would appear to raise it by 39%. Balancing
  the fault-free routes removes almost all of that. This supports NSDI's own
  explanation (tiebreak balancing shifts traffic between dimensions): a
  fault forces a re-optimization, and a re-optimized route set can beat the
  one it replaces.
- **4x8x8 twisted comes out above 100% here, where NSDI measured 98.8%.**
  Our balancing chooses only among shortest dimension-order paths, while
  fault routes may take wild hops and slightly longer paths, so the
  balanced baseline is probably not optimal there.
- **Wild-first routes avoid every failed link with no shortest-path
  fallback** in all nine fault cases, and with 3 VCs their channel
  dependencies are acyclic.

## Caveats

- Our route choice is a heuristic (rip-up and reroute), not NSDI's ILP, and
  it optimizes the busiest link only; NSDI's ILP adds constraints so other
  collectives also perform well. Either can move the kept figure by a few
  percent.
- NSDI does not state the failed dimension; our results are the same for X,
  Y and Z on 4x4x4 and 4x4x8 twisted, and within 0.5% on 4x8x8 twisted.
- One face position per OCS (`scenario one`), as on v4 (48 OCSes for 64
  cubes, one per face position and dimension).
- The bound assumes equal bytes per pair; it ignores injection limits,
  router allocation and timing, which the BookSim runs cover.
- A fixed-bytes BookSim measurement (each pair sends the same amount and
  the run is timed) would test the bound directly. BookSim's batch mode
  ignores the injection rate and so runs every case deep in overload, where
  this torus model collapses; pacing it is future work.
