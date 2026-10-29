# Roadmap

What it would take to rely on this fork's results, and what is planned
beyond that. Items are ticked in the commit that completes them; each links
to the commit, page or study that shows it done.

## 1. Collectives as workloads

BookSim injects open-loop random traffic and reports delivered rate. ML
traffic is collectives: fixed bytes per pair, steps that wait on earlier
steps, judged by completion time. Open-loop measurement misleads under
unequal per-pair load (see the NSDI Table 2 study's caveats), and batch mode
ignores `injection_rate`, so it always runs overloaded.

- [x] Paced batch mode: `batch_paced = 1` honors `injection_rate` in
      `sim_type = batch` (options.md; test `fabric_batch_paced`). Batch
      destinations are still random, so a uniform batch is not equal bytes
      per pair: on 4x4x4 it delivered about 92% of the offered rate even
      below saturation and could not tell one OCS failure from none. The
      message engine below is needed for a true all-to-all.
- [x] Message workload design: [fabric/messages.md](fabric/messages.md)
      (endpoints by router, bytes and ns so one file fits any system,
      dependencies by earlier id, completion time, stall detection).
- [x] Engine: traffic-manager hooks for packet destination and size, fabric
      terminal ranges, `sim_type = messages` with the message file, per-tag
      and per-message output, stall detection (tests `fabric_messages_*`).
      Found on the way: BookSim's default router loses a cycle per packet
      on back-to-back packets of one VC (use `speculative = 1`), and
      upstream's exit status was inverted (fixed).
- [ ] Decide `speculative = 1` for the calibrated system configs: rerun the
      key torus and Boardfly results with it and record the difference.
- [x] Generators in `utils/fabric/collectives.py` (`msg-alltoall`,
      `msg-torus`, `msg-boardfly`, `msg-rings`): stages of ring or direct
      groups, chunks, reduction delay; tests check byte totals and the ring
      all-reduce formula.
- [x] Tree all-reduce (`msg-tree`, `--double` for NCCL-style double binary
      trees), for switched fabrics and latency-bound sizes.
- [ ] In-network and near-network compute ([design](fabric/messages.md#in-network-and-near-network-compute-design)):
      reduction profiles per system (on-chip engines such as TPU 8i's
      Collectives Acceleration Engine and SparseCores); `stream=`
      dependencies for in-stream reduction; compute terminals (`c R N`) so
      switches can reduce and replicate (NVLink SHARP, multicast).
- [ ] Multicast by relay replication: copies made at compute terminals,
      forwarded packet by packet with `stream=`; compared against unicast
      emulation (the source sends N copies, an upper bound). BookSim has no
      multicast: every packet has one destination.
- [ ] Native router multicast (a flit with a destination set, replicated at
      routers), only if results fall between the two bounds above and the
      gap matters: router changes for multi-output allocation and buffer
      release, a multicast route format, and multicast deadlock freedom.
- [ ] Rerun the NSDI Table 2 comparison as a fixed-bytes all-to-all and
      compare with the busiest-link bound.
- [ ] Chakra execution-trace import (MLCommons; used by ASTRA-sim).

## 2. Measurement rigor

- [ ] Structured results: `stats_out` style JSON with every metric, the
      configuration and the fork commit, instead of parsing log text.
- [ ] Run harness: seeds, confidence intervals, saturation from latency-load
      curves, and flags for collapse and for runs that never settled.
- [ ] Studies use the harness; every number in a study comes with its
      spread.

## 3. Validation

- [x] Analytical checks: hop counts, buffer reach, busiest-link bounds
      (tests; tpu-torus.md, tpu-boardfly.md).
- [x] NSDI 2024 Table 2, kept throughput under one OCS failure
      ([study](studies/2026-10-nsdi-table2.md)).
- [x] Google's "up to 50% lower latency" for Boardfly against the torus
      (tpu-boardfly.md).
- [ ] More published numbers: ISCA 2023 (TPU v4) and IEEE Micro 2026.
- [ ] Cross-simulator check of one result in ASTRA-sim (analytical or ns-3
      backend).
- [ ] Measurements on rented Cloud TPU slices (v5e or v6e): all-reduce and
      all-to-all time against message size, to calibrate per-hop latency
      and link throughput in absolute terms.

## 4. Fidelity

- [ ] Bubble flow control (a candidate for 2-VC deadlock freedom on ICI).
- [ ] Link-level retry and bit errors.
- [ ] Failures and OCS reconfiguration during a run.
- [ ] Randomized or weighted multipath routing (NSDI 2024 future work).
- [ ] Per-link utilization output (also feeds the viewer).

## 5. Scale

Cycle-accurate simulation stays at slice scale (up to about 1,024 chips);
long collectives are simulated for a few steady steps and extrapolated.

- [ ] Flow-level model for full pods (9,216-9,600 chips), calibrated
      against BookSim on 1,024-chip slices.
- [ ] Learned surrogate (in the style of m3/m4) if the flow-level model is
      not accurate enough.

## 6. Coverage for more systems

Gaps that matter once systems differ, most important first.

- [ ] Mixed link and endpoint rates: per-link bandwidth (scale-up and
      scale-out links, generations at different rates, half-width links
      after lane failures) and per-endpoint injection and ejection caps
      (DMA engines, HBM bandwidth). BookSim moves one flit per cycle on
      every channel today.
- [ ] Realistic workloads: MoE all-to-all with uneven sizes (ragged, skewed
      experts), pipeline-parallel point-to-point, inference traffic for
      TPU 8i (small latency-bound messages, decode steps, KV-cache moves),
      and a simple compute model so communication can overlap compute
      (with Chakra import, section 1).
- [ ] Scale-out across slices: hierarchical collectives over the datacenter
      network between slices, likely in the flow-level model (section 5)
      rather than BookSim.
- [ ] Lossy, Ethernet-style fabrics (congestion control, packet spraying,
      retransmission) for Ethernet-based scale-up designs. Open decision:
      model them here, or simulate them in a packet simulator built for
      Ethernet (such as htsim) and compare at the collective level.
- [ ] Energy and cost per result: energy per bit by link type (copper,
      optics), BookSim's power model, and OCS and transceiver counts, so
      topologies compare per watt and per dollar.
- [ ] Collective algorithm search: topology-specific schedules (as TACOS
      and PCCL synthesize) in the message format, so a comparison does not
      rest on a hand-written algorithm that suits one topology.

## Viewer

From [viewer/README.md](viewer/README.md).

- [x] Scenes, animated SVGs, VC teaching scenes, docs and previews.
- [ ] Test that scenes build and every path follows real links.
- [ ] GitHub Pages (makes the viewer public; owner's decision).
- [ ] Less cluttered Boardfly copper; bytes per step in the Now panel.
- [ ] Real-time mode with modelled latencies; side-by-side comparison.
- [ ] Failure scenes with fault routes; BookSim per-link utilization and
      packet replay; 1,024-chip slices.
- [ ] Trainium and NVLink scenes.

## Systems and studies

- [ ] Trainium (Trn2, Trn3) and NVLink (NVL72) system pages and exporters.
- [ ] Study: collectives at 1,024 chips, torus against Boardfly.
- [ ] Study: Boardfly under failures, by slice size.
- [ ] Study: topology comparison across systems.
- [ ] Port the earlier Boardfly sweeps (built on a pre-fork BookSim) to
      `topology = fabric`.
