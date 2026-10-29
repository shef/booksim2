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
- [x] `speculative = 1` for calibrated configs: on the NSDI study's
      saturation cases it changes open-loop throughput by 0.7% at most
      (0.0-0.2% in five of six), against 1/9 for a single message with
      8-flit packets. Decision: new studies and message runs use
      `speculative = 1`; existing studies stay as recorded.
- [x] Generators in `utils/fabric/collectives.py` (`msg-alltoall`,
      `msg-torus`, `msg-boardfly`, `msg-rings`): stages of ring or direct
      groups, chunks, reduction delay; tests check byte totals and the ring
      all-reduce formula.
- [x] Tree all-reduce (`msg-tree`, `--double` for NCCL-style double binary
      trees), for switched fabrics and latency-bound sizes.
- [ ] In-network and near-network compute ([design](fabric/messages.md#in-network-and-near-network-compute-design)):
      reduction profiles per system (on-chip engines such as TPU 8i's
      Collectives Acceleration Engine and SparseCores); `stream=`
      dependencies for in-stream reduction (done: test
      `fabric_messages_stream`); compute terminals (`c R N`) so switches
      can reduce and replicate (NVLink SHARP, multicast).
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

- [x] Structured results: `stats_json = FILE` (options.md; test
      `fabric_stats_json`) with status, build commit, full configuration,
      traffic statistics and message results.
- [x] Run harness: `utils/fabric/harness.py` (`seeds`: mean, standard
      deviation and 95% confidence interval of any stats_json metric over N
      seeds; `sweep`: accepted rate and latency against offered load, the
      saturation load and a collapse flag); test `utils_harness`.
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
- [x] Per-link utilization output: `link_util_file` (options.md; test
      `fabric_link_util`). Next: feed it to the viewer.

## 5. Scale

Cycle-accurate simulation stays at slice scale (up to about 1,024 chips);
long collectives are simulated for a few steady steps and extrapolated.

- [ ] Flow-level model for full pods (9,216-9,600 chips), calibrated
      against BookSim on 1,024-chip slices.
- [ ] Learned surrogate (in the style of m3/m4) if the flow-level model is
      not accurate enough.

## 6. Coverage for more systems

Gaps that matter once systems differ, most important first.

- [x] Mixed link and endpoint rates: an optional RATE per link and per
      router's terminals in the topology file (formats.md), a token bucket
      per router output and per injecting terminal (test
      `fabric_link_rates`). For scale-up and scale-out links side by side,
      generations at different rates, reduced-width links, die-to-die links,
      DMA or host-limited endpoints.
- [ ] Multi-die chips: a chip as several routers (one per die or chiplet)
      joined by die-to-die links, with ICI ports, PCIe and HBM attached to
      specific dies (Ironwood's two compute dies; TPU 8i's two compute
      chiplets and separate ICI/SerDes chiplet). D2D rates are not
      published, so sweep them from unlimited (today's model) down to find
      where results change. Prior work: NUNA (arXiv 2608.00867) for
      multi-die GPUs.
- [ ] Study: how much traffic leaves the slice. An analytical model of bytes
      per chip per step crossing the slice by model size, parallelism
      layout (data, pipeline, expert parallel across slices) and collective
      algorithm; then gateway nodes in the topology (chip -> PCIe ~50 GB/s
      -> host -> NIC, about 25 GB/s per chip on TPU 8t per public figures ->
      an ideal datacenter switch at NIC rate) and multislice generators
      (reduce-scatter in slice, all-reduce across slices, all-gather in
      slice), to find whether PCIe, the NIC or the gateway is the limit.
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
