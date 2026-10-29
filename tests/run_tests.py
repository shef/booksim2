#!/usr/bin/env python3
"""BookSim regression and fabric tests.

usage: run_tests.py [BOOKSIM]   (default: src/booksim next to this directory)

  upstream_*   upstream example configs must reproduce tests/golden exactly
               (same seed, same statistics)
  fabric_*     the fabric network on tiny topologies, generated here or read
               from tests/fixtures, checked against analytical values

Exits non-zero if any test fails. Standard library only.
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOOKSIM = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "src", "booksim"))
FIX = os.path.join(HERE, "fixtures")
UTILS = os.path.join(ROOT, "utils", "fabric")
TMP = tempfile.mkdtemp(prefix="booksim_tests_")

UPSTREAM = ["cmeshconfig", "flatflyconfig", "torus88", "mesh88_lat", "singleconfig",
            "anynet/anynet_config"]

BASE = """
topology = fabric;
router = iq;
num_vcs = 2;
vc_buf_size = 64;
wait_for_tail_credit = 0;
vc_allocator = separable_input_first;
sw_allocator = separable_input_first;
alloc_iters = 1;
credit_delay = 2;
routing_delay = 0;
vc_alloc_delay = 1;
sw_alloc_delay = 1;
traffic = uniform;
packet_size = 1;
injection_rate = 0.002;
sim_type = latency;
warmup_periods = 3;
sample_period = 2000;
max_samples = 5;
latency_thres = 100000.0;
"""


def booksim(args, cwd=None, cfg=BASE):
    path = os.path.join(TMP, "base.cfg")
    with open(path, "w") as f:
        f.write(cfg)
    p = subprocess.run([BOOKSIM, path] + args, cwd=cwd or TMP, capture_output=True, text=True,
                       timeout=600)
    return p.stdout + p.stderr


def stat(out, name):
    # Summary lines read "X average = v"; per-sample lines "X average= v".
    m = re.findall(name + r" average ?= ([\d.]+)", out)
    assert m, f"no '{name}' in output:\n{out[-2000:]}"
    return float(m[-1])


def stats_section(text):
    """Simulation output after the configuration echo, without wall time."""
    text = text.split("END Configuration File", 1)[-1]
    return [l for l in text.splitlines()[1:] if not l.startswith("Total run time")]


def write(name, text):
    path = os.path.join(TMP, name)
    with open(path, "w") as f:
        f.write(text)
    return path


# --- topology generators -------------------------------------------------

def torus_dor(k=4, n=3):
    """k-ary n-cube with dor tables: link kind = dim + 4 * dateline."""
    import itertools
    coords = list(itertools.product(range(k), repeat=n))
    rid = {c: i for i, c in enumerate(coords)}
    lines, port = [str(len(coords))], {}
    for c in coords:
        for d in range(n):
            q = list(c)
            q[d] = (q[d] + 1) % k
            q = tuple(q)
            pu, pv = 1 + 2 * d, 2 + 2 * d  # + and - ports
            port[(rid[c], d, 1)], port[(rid[q], d, -1)] = pu, pv
            lines.append(f"{rid[c]} {pu} {rid[q]} {pv} {d + 4 * (c[d] == k - 1)} 10")
    tables = []
    for s in coords:
        for t in coords:
            p = -1
            for d in range(n):
                delta = (t[d] - s[d]) % k
                if delta:
                    sign = 1 if 2 * delta < k or (2 * delta == k and s[d] % 2 == 0) else -1
                    p = port[(rid[s], d, sign)]
                    break
            tables.append(f"{p} {p} {p} 0")
    tables += ["0"] * len(coords)
    return write("torus.txt", "\n".join(lines) + "\n"), write("torus_tables.txt", "\n".join(tables) + "\n")


def switched(chips=4, terms=2, links=2, lat=10):
    """One switch (router `chips`, no terminals) and `chips` chips, each with
    `terms` terminals and `links` parallel links to the switch; lduato tables
    with one escape layer (up to the switch, down to the chip)."""
    sw = chips
    lines = [str(chips + 1), f"t {sw} 0"]
    for c in range(chips):
        lines.append(f"t {c} {terms}")
        for j in range(links):
            lines.append(f"{c} {1 + j} {sw} {1 + c * links + j} 0 {lat}")
    allp = sum(1 << (1 + j) for j in range(links))
    tables = []
    for s in range(chips + 1):
        for d in range(chips + 1):
            if s == d or d == sw:
                tables.append("-1 -1 -1 0 0")
            elif s == sw:
                first = 1 + d * links
                mask = sum(1 << (first + j) for j in range(links))
                tables.append(f"{first} {first} {first} {mask} 0")
            else:
                tables.append(f"1 1 1 {allp} 0")
    tables += [str(allp)] * chips + ["0"]  # arriving at a chip is a down hop
    return write("switched.txt", "\n".join(lines) + "\n"), write("switched_tables.txt", "\n".join(tables) + "\n")


def matrix(endpoints, flows):
    return write("matrix.txt", f"{endpoints}\n" + "".join(f"{s} {d} {w}\n" for s, d, w in flows))


# --- tests ---------------------------------------------------------------

def make_upstream_test(cfg):
    def test():
        name = os.path.basename(cfg)
        d = os.path.join(ROOT, "src", "examples", os.path.dirname(cfg))
        out = booksim_raw(os.path.basename(cfg), d)
        with open(os.path.join(HERE, "golden", name + ".out")) as f:
            golden = stats_section(f.read())
        got = stats_section(out)
        assert got == golden, f"{name}: statistics differ from tests/golden/{name}.out"
    test.__name__ = "upstream_" + os.path.basename(cfg)
    return test


def booksim_raw(cfg, cwd):
    p = subprocess.run([BOOKSIM, cfg], cwd=cwd, capture_output=True, text=True, timeout=600)
    return p.stdout + p.stderr


def fabric_latency_per_hop():
    """Two chips, one link: raising the link latency by 20 cycles raises
    zero-load latency by 20 cycles."""
    lat = []
    for L in (10, 30):
        topo = write("pair.txt", f"2\n0 1 1 1 0 {L}\n")
        paths = write("pair_paths.txt", "0 1 1 1 0\n1 0 1 1 0\n")
        mtx = matrix(2, [(0, 1, 1), (1, 0, 1)])
        out = booksim([f"network_file={topo}", f"fabric_path_file={paths}", "routing_function=src",
                       "traffic=matrix", f"traffic_matrix_file={mtx}"])
        lat.append(stat(out, "Packet latency"))
        assert stat(out, "Hops") == 2.0
    assert abs(lat[1] - lat[0] - 20) < 0.5, lat


def fabric_dor_torus_hops():
    """4-ary 3-cube under dor: mean distance is 3 links (4 routers counting
    the source), uniform traffic includes self-traffic."""
    topo, tables = torus_dor()
    out = booksim([f"network_file={topo}", f"fabric_table_file={tables}", "routing_function=dor"])
    hops = stat(out, "Hops")
    assert abs(hops - 4.0) < 0.05, hops


def fabric_src_torus_fixture():
    d = os.path.join(FIX, "torus444")
    out = booksim([f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
                   "routing_function=src"])
    assert abs(stat(out, "Hops") - 4.0) < 0.05


def fabric_switched_terminals():
    """Per-router terminal counts, a switch with no terminals, parallel
    links, contiguous node numbering and matrix traffic: chip i sends to chip
    i+1 through the switch over two links, so each of its two terminals can
    inject a full flit per cycle."""
    topo, tables = switched()
    mtx = matrix(4, [(i, (i + 1) % 4, 1) for i in range(4)])
    args = [f"network_file={topo}", f"fabric_table_file={tables}", "fabric_esc_layers=1",
            "routing_function=lduato", "traffic=matrix", f"traffic_matrix_file={mtx}",
            "internal_speedup=2.0"]
    out = booksim(args)
    assert stat(out, "Hops") == 3.0
    out = booksim(args + ["sim_type=throughput", "injection_rate=1.0", "max_samples=3"])
    acc = stat(out, "Accepted flit rate")
    assert acc > 0.9, acc  # flits per cycle per terminal


def make_smoke_test(routing, extra):
    def test():
        d = os.path.join(FIX, "bf4")
        out = booksim([f"network_file={d}/topo.txt", f"fabric_table_file={d}/tables.txt",
                       "fabric_esc_layers=2", f"routing_function={routing}", "num_vcs=4",
                       "sim_type=throughput", "injection_rate=1.0", "max_samples=2",
                       "sample_period=1000"] + extra)
        # Deterministic up*/down* and esc saturate low at full overload; the
        # check is that packets keep being delivered and BookSim's deadlock
        # detector stays quiet.
        assert "deadlock" not in out.lower(), "possible deadlock reported"
        assert stat(out, "Accepted flit rate") > 0.005
    test.__name__ = f"fabric_saturated_{routing}"
    return test


def fabric_groupshift():
    d = os.path.join(FIX, "bf4")
    base = [f"network_file={d}/topo.txt", f"fabric_table_file={d}/tables.txt",
            "fabric_esc_layers=2", "routing_function=lduato", "num_vcs=4"]
    out = booksim(base + ["traffic=groupshift(32)"])
    assert stat(out, "Hops") > 2
    out = booksim(base + ["traffic=groupshift(48)"])
    assert "groupshift needs" in out


def fabric_matrix_errors():
    topo, tables = switched()
    bad = matrix(3, [(0, 1, 1), (1, 2, 1), (2, 0, 1)])
    out = booksim([f"network_file={topo}", f"fabric_table_file={tables}", "fabric_esc_layers=1",
                   "routing_function=lduato", "traffic=matrix", f"traffic_matrix_file={bad}"])
    assert "must divide the node count" in out


def fabric_port_gap_error():
    topo = write("gap.txt", "2\n0 1 1 2 0 10\n")
    out = booksim([f"network_file={topo}", "routing_function=src", "fabric_path_file=" + topo])
    assert "unconnected" in out


def fabric_measure_reorder():
    d = os.path.join(FIX, "bf4")
    out = booksim([f"network_file={d}/topo.txt", f"fabric_table_file={d}/tables.txt",
                   "fabric_esc_layers=2", "routing_function=lduato", "num_vcs=4",
                   "measure_reorder=1", "injection_rate=0.05",
                   "vc_buf_size=1024"])  # cover the fixture's 580-cycle fiber round trip
    assert re.search(r"reorder: packets \d+, arrived out of order", out), out[-500:]


def exporter(script, *args):
    p = subprocess.run([sys.executable, os.path.join(UTILS, script)] + list(args),
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-2000:]
    return p.stdout


def utils_boardfly_collectives():
    """boardfly.py and collectives.py end to end: a 2-group slice with an
    optic failure (escape graph verified acyclic), then the overlapped
    hierarchical all-reduce at six terminals per chip."""
    d = os.path.join(TMP, "bf2")
    out = exporter("boardfly.py", d, "--groups", "2", "--optic", "1", "--verify")
    assert '"escape_acyclic": true' in out, out
    mtx = os.path.join(TMP, "bf2_ar.txt")
    exporter("collectives.py", "boardfly", mtx, "--groups", "2", "--stage", "fused")
    out = booksim([f"network_file={d}/topo.txt", f"fabric_table_file={d}/tables.txt",
                   "fabric_esc_layers=2", "routing_function=lduato", "num_vcs=4",
                   "fabric_terminals=6", "traffic=matrix", f"traffic_matrix_file={mtx}",
                   "vc_buf_size=1024"])
    assert stat(out, "Hops") > 2


def utils_torus_fault_routes():
    """torus.py fault routes with one OCS failure in Z: they avoid the failed
    links (the exporter asserts it), their channel dependencies are acyclic
    (one 4x4x4 cube with 2 VCs, twisted 4x4x8 with 3), and a saturated run
    reports no deadlock."""
    for name, dims, extra, vcs, failed in (("t444", "444", [], 2, 2),
                                           ("t448tw", "448", ["--twisted", "--vcs", "3"], 3, 4)):
        d = os.path.join(TMP, name)
        out = exporter("torus.py", d, "--dims", *dims, "--ocs-fault", "z",
                       "--scenario", "adjacent", "--ocs-distance", "2", *extra)
        assert f"({failed} failed)" in out and "acyclic" in out and f"{vcs} VCs" in out, out
        out = booksim([f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
                       "routing_function=src", f"num_vcs={vcs}", "sim_type=throughput",
                       "injection_rate=1.0", "max_samples=2", "sample_period=1000"])
        assert "deadlock" not in out.lower(), "possible deadlock reported"
        assert stat(out, "Accepted flit rate") > 0.05


def utils_cdgcheck():
    """cdgcheck.py passes fault-free routes and finds the channel dependency
    cycle in twisted 4x4x8 fault routes that share 2 VCs with wild hops."""
    d = os.path.join(TMP, "t448tw_free")
    exporter("torus.py", d, "--dims", "4", "4", "8", "--twisted")
    assert "acyclic" in exporter("cdgcheck.py", f"{d}/topo.txt", f"{d}/paths.txt")
    d = os.path.join(TMP, "t448tw_v2")
    out = exporter("torus.py", d, "--dims", "4", "4", "8", "--twisted", "--ocs-fault", "z")
    assert "CYCLE" in out, out
    p = subprocess.run([sys.executable, os.path.join(UTILS, "cdgcheck.py"), f"{d}/topo.txt",
                        f"{d}/paths.txt"], capture_output=True, text=True, timeout=600)
    assert p.returncode == 1 and "cycle of" in p.stdout, p.stdout


def utils_torus_twisted_hops():
    """Twisted 4x4x8: shortest dimension-order routes average fewer hops
    than the plain torus (3.5 links vs 4)."""
    hops = {}
    for tw in (False, True):
        d = os.path.join(TMP, f"t448_{tw}")
        exporter("torus.py", d, "--dims", "4", "4", "8", *(["--twisted"] if tw else []))
        out = booksim([f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
                       "routing_function=src"])
        hops[tw] = stat(out, "Hops")
    assert hops[True] < hops[False] - 0.2, hops


def fabric_batch_paced():
    """batch_paced = 1 makes a batch follow injection_rate: below saturation,
    halving the rate about doubles the batch time (the batch is long enough
    that fiber latency at the tail is small). Upstream batch mode
    (the default) ignores the rate."""
    d = os.path.join(TMP, "t444_batch")
    exporter("torus.py", d, "--dims", "4", "4", "4")
    args = [f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
            "routing_function=src", "sim_type=batch", "batch_size=300", "batch_count=1"]

    def duration(rate, paced):
        out = booksim(args + [f"injection_rate={rate}", f"batch_paced={paced}"])
        m = re.findall(r"Overall min batch duration = (\d+)", out)
        assert m, out[-2000:]
        return int(m[0])
    fast, slow = duration(0.01, 1), duration(0.005, 1)
    assert 1.7 < slow / fast < 2.3, (fast, slow)
    assert duration(0.01, 0) == duration(0.005, 0)


TESTS = [make_upstream_test(c) for c in UPSTREAM] + [
    fabric_latency_per_hop, fabric_dor_torus_hops, fabric_src_torus_fixture,
    fabric_switched_terminals, fabric_groupshift, fabric_matrix_errors, fabric_port_gap_error,
    fabric_measure_reorder, utils_boardfly_collectives, utils_torus_fault_routes,
    utils_cdgcheck, utils_torus_twisted_hops, fabric_batch_paced,
] + [make_smoke_test(r, x) for r, x in (("lduato", []), ("duato", []), ("updown", []),
                                        ("esc", []), ("ugal", ["ugal_threshold=128"]))]


def main():
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}", flush=True)
        except Exception as e:  # report every failure, then exit non-zero
            failed += 1
            print(f"FAIL {t.__name__}: {e}", flush=True)
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
