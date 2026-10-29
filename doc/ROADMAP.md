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

- [ ] Paced batch mode: honor `injection_rate` in `sim_type = batch`, so a
      fixed-bytes run is a clean all-to-all completion time.
- [ ] Message engine: a schedule file of messages (source, destination,
      bytes, dependencies on earlier messages); BookSim injects each when its
      dependencies have arrived and reports per-step and total completion
      time.
- [ ] Generators in `utils/fabric/collectives.py` for that format: ring
      all-reduce (torus, Boardfly), hierarchical all-reduce, all-to-all.
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
