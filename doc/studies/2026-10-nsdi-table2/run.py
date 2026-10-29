#!/usr/bin/env python3
"""NSDI 2024 Table 2 reproduction (2026-10-nsdi-table2.md).

For 4x4x4, 4x4x8 twisted and 4x8x8 twisted slices, exports routes with
utils/fabric/torus.py (fault-free: parity tiebreak and --balance; one OCS
unavailable in X, Y or Z: wild-first fault routes, scenario `one`, 3 VCs),
then:
  bounds  all-to-all throughput from the routes alone: (chips - 1) / the
          most paths on any link, in links per chip (one path per pair,
          equal bytes per pair, as NSDI's ILP assumes);
  sweep   BookSim uniform traffic from 6 terminals per chip at offered loads
          around each bound; saturation = the highest accepted rate.

usage: run.py WORKDIR [bounds|sweep|all]    (writes WORKDIR/bounds.csv, sweep.csv)
"""
import collections, concurrent.futures as cf, csv, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "utils", "fabric"))
import cdgcheck  # noqa: E402

BOOKSIM = os.path.join(ROOT, "src", "booksim")
SHAPES = {"444": ["4", "4", "4"], "448tw": ["4", "4", "8", "--twisted"], "488tw": ["4", "8", "8", "--twisted"]}
CASES = {"none": [], "bal": ["--balance"],
         **{f: ["--ocs-fault", f, "--scenario", "one", "--vcs", "3"] for f in "xyz"}}
FRACS = [0.55, 0.65, 0.75, 0.82, 0.88, 0.94, 1.0, 1.08]
PACKET = 8  # flits (booksim.cfg)


def export(work, shape, case):
    d = os.path.join(work, f"{shape}_{case}")
    if not os.path.exists(os.path.join(d, "paths.txt")):
        subprocess.run([sys.executable, os.path.join(ROOT, "utils/fabric/torus.py"), d,
                        "--dims", *SHAPES[shape], *CASES[case]], check=True, capture_output=True)
    return d


def bound(d):
    nxt = cdgcheck.load_links(os.path.join(d, "topo.txt"))
    load, n = collections.Counter(), 0
    for line in open(os.path.join(d, "paths.txt")):
        x = list(map(int, line.split()))
        if not x or x[0] == x[1]:
            continue
        r = x[0]
        for i in range(x[2]):
            p = x[3 + 2 * i]
            load[(r, p)] += 1
            r = nxt[(r, p)]
        n = max(n, x[0] + 1)
    return n, max(load.values()), (n - 1) / max(load.values())


def simulate(d, offered):
    cmd = [BOOKSIM, os.path.join(HERE, "booksim.cfg"), f"network_file={d}/topo.txt",
           f"fabric_path_file={d}/paths.txt", "routing_function=src", "num_vcs=3",
           "buffer_policy=shared", "buf_size=5242", "private_buf_size=16", "internal_speedup=2.0",
           "sim_type=throughput", "sample_period=5000", "max_samples=6", "fabric_terminals=6",
           f"injection_rate={offered / 6 / PACKET:.6f}"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    acc = re.findall(r"Accepted flit rate average ?= ([\d.]+)", out)
    return float(acc[-1]) * 6 if acc else None, "Time taken" in out


def main():
    work, what = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "all")
    os.makedirs(work, exist_ok=True)
    jobs = [(s, c) for s in SHAPES for c in CASES]
    with cf.ProcessPoolExecutor() as ex:
        dirs = dict(zip(jobs, ex.map(export, [work] * len(jobs), *zip(*jobs))))
    bounds = {}
    with open(os.path.join(work, "bounds.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["shape", "routes", "chips", "busiest_link_paths", "bound_links_per_chip"])
        for (s, c), d in dirs.items():
            n, m, b = bound(d)
            bounds[(s, c)] = b
            w.writerow([s, c, n, m, f"{b:.4f}"])
    if what == "bounds":
        return
    runs = [(s, c, round(bounds[(s, "bal")] * fr, 4)) for s, c in jobs for fr in FRACS]
    with open(os.path.join(work, "sweep.csv"), "w", newline="") as f, cf.ProcessPoolExecutor() as ex:
        w = csv.writer(f)
        w.writerow(["shape", "routes", "offered", "accepted", "drained"])
        futs = {ex.submit(simulate, dirs[(s, c)], o): (s, c, o) for s, c, o in runs}
        for fu in cf.as_completed(futs):
            acc, drained = fu.result()
            w.writerow([*futs[fu], f"{acc:.4f}" if acc is not None else "", drained])
            f.flush()


if __name__ == "__main__":
    main()
