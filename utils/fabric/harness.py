#!/usr/bin/env python3
"""Run BookSim with seeds and confidence intervals, or sweep offered load to
find saturation (doc/ROADMAP.md, section 2). Reads each run's stats_json, so
it never parses BookSim's text output.

  seeds   run CONFIG [KEY=VALUE ...] with seeds 1..N in parallel; print the
          mean, standard deviation and 95% confidence interval of each
          METRIC (section.key in the JSON, e.g. messages.completion_ns)
  sweep   run at each offered load (injection_rate, flits per cycle per
          terminal unless --packets) with N seeds; report accepted rate and
          latency per load, the saturation load (the last load where
          accepted keeps up with offered within --tol) and whether accepted
          falls after its peak (post-saturation collapse, where open-loop
          numbers are unreliable)

Writes all results as JSON with --out.

usage: harness.py seeds CONFIG [KEY=VALUE ...] --metric M [--metric M] [--n N]
       harness.py sweep CONFIG [KEY=VALUE ...] --loads L1,L2,... [--n N]
                    [--packet-size P] [--tol T]
       common: [--booksim PATH] [--jobs J] [--out FILE]
"""

import argparse
import concurrent.futures as cf
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKSIM = os.path.join(HERE, "..", "..", "src", "booksim")

# Student t, two-sided 95%, by degrees of freedom (n - 1); 1.96 beyond.
T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26,
       10: 2.23, 15: 2.13, 20: 2.09, 30: 2.04}


def t95(df):
    if df in T95:
        return T95[df]
    smaller = [k for k in T95 if k < df]
    return T95[max(smaller)] if df < 30 else 1.96


def run_once(booksim, config, overrides, seed, out):
    """One run from the caller's directory (relative paths in the config and
    overrides resolve as on the command line)."""
    cmd = [booksim, config, *overrides, f"seed={seed}", f"stats_json={out}"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        with open(out) as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {"run": {"status": f"no result (exit {p.returncode})"}}
    r["_exit"] = p.returncode
    return r


def get(r, metric):
    section, key = metric.split(".", 1)
    v = r.get(section, {}).get(key)
    return v if isinstance(v, (int, float)) else None


def summarize(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    n = len(vals)
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    half = t95(n - 1) * sd / math.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "mean": mean, "sd": sd, "ci95": half, "min": min(vals), "max": max(vals)}


def run_many(args, jobs_spec):
    with tempfile.TemporaryDirectory() as tmp, cf.ProcessPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(run_once, args.booksim, args.config, ov, seed,
                          os.path.join(tmp, f"run{i}.json"))
                for i, (ov, seed) in enumerate(jobs_spec)]
        return [f.result() for f in futs]


def cmd_seeds(args):
    results = run_many(args, [(args.overrides, s) for s in range(1, args.n + 1)])
    bad = [r["run"].get("status") for r in results if r["run"].get("status") != "ok"]
    report = {"runs": args.n, "not_ok": bad, "metrics": {}}
    for m in args.metric:
        s = summarize([get(r, m) for r in results])
        report["metrics"][m] = s
        if s["n"]:
            print(f"{m}: mean {s['mean']:.6g}  sd {s['sd']:.3g}  95% CI +/- {s['ci95']:.3g}"
                  f"  ({s['n']} runs, range {s['min']:.6g}..{s['max']:.6g})")
        else:
            print(f"{m}: no values")
    if bad:
        print(f"warning: {len(bad)} runs not ok: {sorted(set(bad))}")
    return report


def cmd_sweep(args):
    loads = [float(x) for x in args.loads.split(",")]
    spec = []
    for load in loads:
        rate = load / args.packet_size if args.packets else load
        for s in range(1, args.n + 1):
            spec.append((args.overrides + [f"injection_rate={rate}"], s))
    results = run_many(args, spec)
    rows = []
    for i, load in enumerate(loads):
        rs = results[i * args.n:(i + 1) * args.n]
        acc = summarize([get(r, "traffic_class_0.accepted_flit_rate_avg") for r in rs])
        lat = summarize([get(r, "traffic_class_0.packet_latency_avg") for r in rs])
        ok = sum(r["run"].get("status") == "ok" for r in rs)
        rows.append({"offered": load, "accepted": acc, "latency": lat, "ok_runs": ok})
    sat, peak = None, None
    for row in rows:
        a = row["accepted"].get("mean")
        if a is None:
            continue
        if a >= row["offered"] * (1 - args.tol):
            sat = row["offered"]
        if peak is None or a > peak:
            peak = a
    last = rows[-1]["accepted"].get("mean")
    collapse = peak is not None and last is not None and last < peak * (1 - args.tol)
    print(f"{'offered':>8} {'accepted':>10} {'+/-':>8} {'latency':>10} {'ok':>4}")
    for row in rows:
        a, l = row["accepted"], row["latency"]
        print(f"{row['offered']:8.4g} {a.get('mean', float('nan')):10.4g} {a.get('ci95', float('nan')):8.2g}"
              f" {l.get('mean', float('nan')):10.4g} {row['ok_runs']:>4}")
    print(f"saturation load: {sat}   peak accepted: {peak}"
          + ("   COLLAPSE: accepted falls past saturation" if collapse else ""))
    return {"rows": rows, "saturation_load": sat, "peak_accepted": peak, "collapse": collapse}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seeds", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("config")
        p.add_argument("--n", type=int, default=5, help="seeds per point")
        p.add_argument("--booksim", default=BOOKSIM)
        p.add_argument("--jobs", type=int, default=os.cpu_count())
        p.add_argument("--out")
    sub.choices["seeds"].add_argument("--metric", action="append", required=True)
    sw = sub.choices["sweep"]
    sw.add_argument("--loads", required=True)
    sw.add_argument("--packets", action="store_true",
                    help="loads are flits per cycle; divide by --packet-size for injection_rate")
    sw.add_argument("--packet-size", type=int, default=1)
    sw.add_argument("--tol", type=float, default=0.03)
    # KEY=VALUE arguments anywhere are BookSim overrides.
    a, extra = ap.parse_known_args()
    a.overrides = [x for x in extra if "=" in x and not x.startswith("-")]
    unknown = [x for x in extra if x not in a.overrides]
    if unknown:
        ap.error(f"unrecognized arguments: {' '.join(unknown)}")
    a.booksim = os.path.abspath(a.booksim)
    report = cmd_seeds(a) if a.cmd == "seeds" else cmd_sweep(a)
    report["command"] = sys.argv
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1)


if __name__ == "__main__":
    main()
