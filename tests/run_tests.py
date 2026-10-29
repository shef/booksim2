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


def _write(d, **files):
    os.makedirs(d, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(d, name + ".txt"), "w") as f:
            f.write(text)
    return d


def _messages(d, extra=(), check=True):
    """Run sim_type = messages on d/topo.txt, d/paths.txt, d/msgs.txt; return
    (completion cycles, {id: (ready, first_inject, finish)}, output)."""
    out_file = os.path.join(d, "out.txt")
    p = subprocess.run([BOOKSIM, os.path.join(TMP, "base.cfg"), f"network_file={d}/topo.txt",
                        f"fabric_path_file={d}/paths.txt", "routing_function=src",
                        "sim_type=messages", f"message_file={d}/msgs.txt",
                        f"message_out_file={out_file}", "packet_size=8", "speculative=1",
                        *extra], capture_output=True, text=True, timeout=600)
    out = p.stdout + p.stderr
    if not check:
        return p.returncode, out
    assert p.returncode == 0, out[-2000:]
    m = re.search(r"Message completion time = (\d+)", out)
    assert m, out[-2000:]
    per = {}
    for line in open(out_file):
        if not line.startswith("#"):
            i, _, ready, first, fin = line.split()
            per[int(i)] = (int(ready), int(first), int(fin))
    return int(m.group(1)), per, out


# A line of chips 0 - 1 - 2 at 10 cycles per hop.
LINE_TOPO = "3\n0 1 1 1 0 10\n1 2 2 1 0 10\n"


def fabric_messages_basic():
    """One message takes zero-load latency plus one cycle per flit; two
    messages over the same link take twice the serialization; a dependency
    starts the next message after its delay."""
    booksim([])  # writes base.cfg
    d = _write(os.path.join(TMP, "msg_basic"), topo=LINE_TOPO,
               paths="0 1 1 1 0\n1 0 1 1 0\n0 2 2 1 0 2 0\n2 0 2 1 0 1 0\n1 2 1 2 0\n2 1 1 1 0\n",
               msgs="0 0 2 200\n")
    lat, _, _ = _messages(d)
    _write(d, msgs="0 0 2 160000\n")              # 800 flits
    one, _, _ = _messages(d)
    assert abs(one - (lat + 799)) <= 3, (lat, one)
    _write(d, msgs="0 0 2 160000\n1 0 2 160000\n")
    two, per, _ = _messages(d)
    assert abs(two - (lat + 1599)) <= 4, (lat, two)
    assert abs(per[0][2] - per[1][2]) < 20, per   # fair sharing: both finish together
    _write(d, msgs="0 0 2 160000 tag=a\n1 2 0 160000 after=0 delay=500 tag=b\n")
    chain, per, out = _messages(d)
    assert per[1][0] == per[0][2] + 500, per
    assert abs(chain - (2 * one + 500)) <= 4, (one, chain)
    assert "Message tag b: 1 messages" in out


def fabric_messages_endpoints():
    """Endpoints are routers with terminals: a switch in the middle is not an
    endpoint, a chip with 3 terminals sends to one with 1, and messages to a
    switch are refused."""
    d = _write(os.path.join(TMP, "msg_switch"),
               topo="3\nt 0 3\nt 1 0\n0 1 1 1 0 10\n1 2 2 1 0 10\n",
               paths="0 2 2 1 0 2 0\n2 0 2 1 0 1 0\n0 1 1 1 0\n1 0 1 1 0\n1 2 1 2 0\n2 1 1 1 0\n",
               msgs="0 0 2 16000\n1 2 0 16000 after=0\n")
    _, per, _ = _messages(d)
    assert per[1][2] > per[0][2] > 0, per
    _write(d, msgs="0 0 1 16000\n")
    code, out = _messages(d, check=False)
    assert code != 0 and "no terminals" in out, out[-500:]
    _write(d, msgs="0 0 2 100\n1 2 0 100 after=7\n")
    code, out = _messages(d, check=False)
    assert code != 0 and "earlier message ids" in out, out[-500:]


def fabric_messages_stall():
    """Four chips on a one-way ring, each sending two hops ahead on one VC
    with small buffers, deadlock; the run stops at the stall limit with exit
    status 2 and names the stuck messages."""
    d = _write(os.path.join(TMP, "msg_ring"),
               topo="4\n" + "".join(f"{r} 1 {(r + 1) % 4} 2 0 10\n" for r in range(4)),
               paths="".join(f"{s} {(s + k) % 4} {k} " + " ".join(["1 0"] * k) + "\n"
                             for s in range(4) for k in (1, 2, 3)),
               msgs="".join(f"{s} {s} {(s + 2) % 4} 40000\n" for s in range(4)))
    code, out = _messages(d, ["num_vcs=1", "vc_buf_size=4", "message_stall_cycles=5000"],
                          check=False)
    assert code == 2 and "Message run stalled" in out, out[-1500:]
    assert out.count("  message ") == 4, out[-1500:]


def utils_collective_messages():
    """Generated all-reduces move 2(N-1) x S in total (reduce-scatter plus
    all-gather, or a tree's reduce plus broadcast) on the torus, Boardfly
    and trees; an all-to-all sends S / N per pair; dependencies name earlier
    messages."""
    S = 6_000_000
    cases = [("msg-torus", ["--dims", "4", "4", "4"], 64),
             ("msg-boardfly", ["--groups", "2"], 64),
             ("msg-alltoall", ["--endpoints", "16"], 16),
             ("msg-tree", ["--endpoints", "13", "--chunks", "3"], 13),
             ("msg-tree", ["--endpoints", "13", "--double"], 13)]
    for cmd, args, n in cases:
        out = os.path.join(TMP, f"{cmd}.txt")
        exporter("collectives.py", cmd, out, "--bytes", str(S), *args)
        total, seen = 0, set()
        for line in open(out):
            if line.startswith("#"):
                continue
            f = line.split()
            total += int(f[3])
            for x in f[4:]:
                if x.startswith("after="):
                    assert all(int(d) in seen for d in x[6:].split(",")), line
            seen.add(int(f[0]))
        want = S * (n - 1) / n * (1 if cmd == "msg-alltoall" else 2) * n
        assert abs(total - want) / want < 0.002, (cmd, total, want)


def fabric_messages_ring_allreduce():
    """Bidirectional ring all-reduce on an 8-chip ring takes 2(n-1) steps of
    (S / 2n) bytes each plus one neighbour latency per step."""
    n = 8
    d = _write(os.path.join(TMP, "msg_ring8"),
               topo=f"{n}\nt 0 2\n" + "".join(f"t {r} 2\n" for r in range(1, n))
               + "".join(f"{r} 1 {(r + 1) % n} 2 0 10\n" for r in range(n)),
               paths="".join(f"{s} {(s + k) % n} {min(k, n - k)} "
                             + " ".join(["1 0" if k <= n // 2 else "2 0"] * min(k, n - k))
                             + "\n" for s in range(n) for k in range(1, n)),
               msgs="0 0 1 200\n")
    lat, _, _ = _messages(d)
    S = 3_200_000                                     # shard 200 KB = 1,000 flits
    exporter("collectives.py", "msg-rings", f"{d}/msgs.txt", f"{d}/topo.txt",
             "--bytes", str(S), "--k", "1")
    t, _, out = _messages(d)
    want = 2 * (n - 1) * (lat + 1000 - 1)
    assert abs(t - want) / want < 0.03, (t, want, lat)
    assert "Message tag ag0.6" in out


def fabric_link_rates():
    """A link rate below 1 (topology file's optional last field) slows that
    link: an 800-flit message over a half-rate hop takes twice the
    serialization, over a quarter-rate hop four times; terminal rates
    (`t R N RATE`) cap injection at the source and ejection at the
    destination the same way; a bad rate is refused."""
    paths = "0 1 1 1 0\n1 0 1 1 0\n0 2 2 1 0 2 0\n2 0 2 1 0 1 0\n1 2 1 2 0\n2 1 1 1 0\n"
    times = {}
    for rate in ("", " 0.5", " 0.25", " 0.3"):
        d = _write(os.path.join(TMP, f"rate{rate.strip() or '1'}"),
                   topo=f"3\n0 1 1 1 0 10\n1 2 2 1 0 10{rate}\n", paths=paths,
                   msgs="0 0 2 160000\n")
        times[rate.strip() or "1"], _, _ = _messages(d)
    ser = {r: times[r] - (times["1"] - 800) for r in times}   # minus latency
    assert abs(ser["0.5"] / 800 - 2) < 0.05, times
    assert abs(ser["0.25"] / 800 - 4) < 0.05, times
    assert abs(ser["0.3"] / 800 - 1 / 0.3) < 0.05, times     # rates that do not divide 1
    for name, t in (("inject", "t 0 1 0.5\n"), ("eject", "t 2 1 0.25\n")):
        d = _write(os.path.join(TMP, f"rate_{name}"),
                   topo=f"3\n{t}0 1 1 1 0 10\n1 2 2 1 0 10\n", paths=paths,
                   msgs="0 0 2 160000\n")
        t_cap, _, _ = _messages(d)
        want = 2 if name == "inject" else 4
        assert abs((t_cap - (times["1"] - 800)) / 800 - want) < 0.05, (name, t_cap, times)
    d = _write(os.path.join(TMP, "rate_bad"), topo="3\n0 1 1 1 0 10\n1 2 2 1 0 10 1.5\n",
               paths=paths, msgs="0 0 2 200\n")
    code, out = _messages(d, check=False)
    assert code != 0 and "link rate" in out, out[-500:]


def fabric_stats_json():
    """stats_json writes one JSON file per run: status, build commit, every
    configuration key, overall traffic statistics, and message results;
    a stalled message run still writes it, with status "stalled"."""
    import json
    d = os.path.join(FIX, "torus444")
    out = os.path.join(TMP, "open.json")
    booksim([f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
             "routing_function=src", f"stats_json={out}"])
    r = json.load(open(out))
    assert r["run"]["status"] == "ok" and r["run"]["build_commit"], r["run"]
    assert r["config"]["routing_function"] == "src" and r["config"]["num_vcs"] == 2
    assert r["traffic_class_0"]["hops_avg"] > 0, r["traffic_class_0"]
    d = os.path.join(TMP, "msg_basic")                  # from fabric_messages_basic
    out = os.path.join(TMP, "msg.json")
    _messages(d, [f"stats_json={out}"])
    r = json.load(open(out))
    assert r["messages"]["completion_cycles"] > 0 and r["messages"]["tags"], r["messages"]
    d = os.path.join(TMP, "msg_ring")                   # from fabric_messages_stall
    out = os.path.join(TMP, "stall.json")
    code, _ = _messages(d, ["num_vcs=1", "vc_buf_size=4", "message_stall_cycles=5000",
                            f"stats_json={out}"], check=False)
    r = json.load(open(out))
    assert code == 2 and r["run"]["status"] == "stalled" and r["messages"]["unfinished"] == 4, r


def utils_harness():
    """harness.py seeds reports a mean and confidence interval from
    stats_json; sweep finds the saturation load of a small torus."""
    import json
    booksim([])  # writes base.cfg
    d = os.path.join(FIX, "torus444")
    common = [f"network_file={d}/topo.txt", f"fabric_path_file={d}/paths.txt",
              "routing_function=src", "--booksim", BOOKSIM, "--jobs", "4"]
    out = os.path.join(TMP, "seeds.json")
    exporter("harness.py", "seeds", os.path.join(TMP, "base.cfg"), *common, "--n", "3",
             "--metric", "traffic_class_0.packet_latency_avg", "--out", out)
    r = json.load(open(out))["metrics"]["traffic_class_0.packet_latency_avg"]
    assert r["n"] == 3 and r["mean"] > 0 and r["ci95"] >= 0, r
    out = os.path.join(TMP, "sweep.json")
    exporter("harness.py", "sweep", os.path.join(TMP, "base.cfg"), *common, "--n", "1",
             "--loads", "0.05,0.2,0.9", "sim_type=throughput", "--out", out)
    r = json.load(open(out))
    assert r["saturation_load"] in (0.05, 0.2), r["saturation_load"]
    assert r["rows"][0]["accepted"]["mean"] > 0.04, r["rows"][0]


def fabric_link_util():
    """link_util_file counts the flits on each link direction: an 800-flit
    message 0 -> 2 on a line puts exactly 800 on 0 -> 1 and 1 -> 2 and none
    on the reverse directions; stats_json gets the busiest link."""
    import csv
    import json
    d = _write(os.path.join(TMP, "util"), topo="3\n0 1 1 1 0 10\n1 2 2 1 0 10\n",
               paths="0 1 1 1 0\n1 0 1 1 0\n0 2 2 1 0 2 0\n2 0 2 1 0 1 0\n1 2 1 2 0\n2 1 1 1 0\n",
               msgs="0 0 2 160000\n")
    util, js = os.path.join(d, "util.csv"), os.path.join(d, "r.json")
    _messages(d, [f"link_util_file={util}", f"stats_json={js}"])
    flits = {(int(r["router"]), int(r["to_router"])): int(r["flits"]) for r in csv.DictReader(open(util))}
    assert flits == {(0, 1): 800, (1, 0): 0, (1, 2): 800, (2, 1): 0}, flits
    links = json.load(open(js))["links"]
    assert links["count"] == 4 and 0 < links["max_utilization"] <= 1, links


def fabric_messages_stream():
    """stream= forwards packet by packet: relaying 0 -> 1 -> 2 takes about
    one message time with stream= and two with after=; delay= applies to
    each streamed packet; a dependent of a different size sends its share
    in step with the source."""
    paths = "0 1 1 1 0\n1 0 1 1 0\n0 2 2 1 0 2 0\n2 0 2 1 0 1 0\n1 2 1 2 0\n2 1 1 1 0\n"
    d = _write(os.path.join(TMP, "stream"), topo=LINE_TOPO, paths=paths, msgs="0 0 1 160000\n")
    one, _, _ = _messages(d)
    _write(d, msgs="0 0 1 160000\n1 1 2 160000 after=0\n")
    whole, _, _ = _messages(d)
    _write(d, msgs="0 0 1 160000\n1 1 2 160000 stream=0\n")
    stream, _, _ = _messages(d)
    assert abs(whole - 2 * one) < 0.03 * whole, (one, whole)
    assert stream < one * 1.15, (one, stream)
    _write(d, msgs="0 0 1 160000\n1 1 2 160000 stream=0 delay=300\n")
    delayed, _, _ = _messages(d)
    assert abs(delayed - stream - 300) <= 5, (stream, delayed)
    _write(d, msgs="0 0 1 160000\n1 1 2 80000 stream=0\n")
    half, per, _ = _messages(d)
    assert per[1][2] - per[0][2] < 60, per        # finishes with its source
    assert per[1][1] < per[0][2] / 2, per         # but starts early


def fabric_compute_terminals():
    """Compute terminals (`c R N`) let a switch reduce: four chips stream
    their data into the switch's engine, which streams the result back.
    Each chip's link then carries S once each way, against 2(n-1)/n S = 1.5 S
    for a ring all-reduce over the same chips, so it is about a third
    faster; messages address compute terminals as cR."""
    S, n = 160000, 4
    topo = f"5\nt 4 0\nc 4 {n}\n" + "".join(f"{i} 1 4 {i + 1} 0 10\n" for i in range(n))
    paths = "".join(f"{i} 4 1 1 0\n4 {i} 1 {i + 1} 0\n" for i in range(n))
    paths += "".join(f"{i} {j} 2 1 0 {j + 1} 0\n" for i in range(n) for j in range(n) if i != j)
    up = "".join(f"{i} {i} c4 {S}\n" for i in range(n))
    down = "".join(f"{n + j} c4 {j} {S} stream={','.join(map(str, range(n)))} delay=20\n"
                   for j in range(n))
    d = _write(os.path.join(TMP, "innet"), topo=topo, paths=paths, msgs=up + down)
    t_net, _, out = _messages(d)
    assert "compute terminals on 1 routers" in out, out[:2000]
    ring, mid, prev = [], 0, [None] * n            # unidirectional ring, 2(n-1) steps
    for k in range(2 * (n - 1)):
        got = [None] * n
        for i in range(n):
            dep = f" after={prev[i]}" if prev[i] is not None else ""
            ring.append(f"{mid} {i} {(i + 1) % n} {S // n}{dep}\n")
            got[(i + 1) % n] = mid
            mid += 1
        prev = got
    _write(d, msgs="".join(ring))
    t_ring, _, _ = _messages(d)
    assert t_net < 0.8 * t_ring, (t_net, t_ring)
    _write(d, msgs="0 0 c3 100\n")
    code, out = _messages(d, check=False)
    assert code != 0 and "no compute terminals" in out, out[-500:]


def fabric_eject_any():
    """fabric_eject_any = 1 lets a packet leave through any terminal of its
    destination: four chips sending to one chip with four terminals through
    four switches (incast) then use all four ejection ports, about twice as
    fast as with each packet's terminal fixed at the source. Also checks
    clos.py's routes are deadlock-free."""
    d = os.path.join(TMP, "clos")
    exporter("clos.py", d, "--chips", "8", "--switches", "4")
    assert "acyclic" in exporter("cdgcheck.py", f"{d}/topo.txt", f"{d}/paths.txt")
    _write(d, msgs="".join(f"{i - 1} {i} 0 1600000\n" for i in range(1, 5)))
    big = ["num_vcs=4", "vc_buf_size=512"]          # cover the switch round trip
    fixed, _, _ = _messages(d, big)
    anyp, _, _ = _messages(d, big + ["fabric_eject_any=1"])
    assert anyp < 0.6 * fixed, (fixed, anyp)


def fabric_multipath():
    """Repeated path lines for a pair are alternatives, chosen per packet:
    one message between two chips of a 4-switch Clos uses all four switches
    with --multipath, about four times faster than its single path."""
    big = ["num_vcs=4", "vc_buf_size=512", "fabric_eject_any=1"]
    times = {}
    for mp in ("", "--multipath"):
        d = os.path.join(TMP, f"clos_mp{mp}")
        exporter("clos.py", d, "--chips", "4", "--switches", "4", *([mp] if mp else []))
        _write(d, msgs="0 0 1 1600000\n")
        times[mp], _, _ = _messages(d, big)
    assert times["--multipath"] < 0.35 * times[""], times


def fabric_multicast_relay():
    """Multicast by relay: broadcasting from one chip of an 8-chip, 4-switch
    Clos through the switches' compute terminals sends the data once, about
    (N - 1) / 1 times less than sending every copy from the root."""
    d = os.path.join(TMP, "clos_bcast")
    exporter("clos.py", d, "--chips", "8", "--switches", "4", "--compute", "8", "--multipath")
    big = ["num_vcs=4", "vc_buf_size=512", "fabric_eject_any=1"]
    t = {}
    for mode in ("unicast", "relay"):
        exporter("collectives.py", "msg-bcast", f"{d}/msgs.txt", "--bytes", "1600000",
                 "--endpoints", "8", "--mode", mode, "--engines", "8,9,10,11")
        t[mode], _, _ = _messages(d, big)
    assert t["relay"] < t["unicast"] / 4, t


def utils_viewer_scenes():
    """scenes.py rebuilds the viewer's committed scene files exactly, and in
    every fabric scene each transfer moves only along links of the scene."""
    import json
    out = os.path.join(TMP, "scenes")
    exporter("scenes.py", out)
    data = os.path.join(ROOT, "doc", "viewer", "data")
    for name in sorted(os.listdir(data)):
        with open(os.path.join(data, name)) as f:
            committed = f.read()
        with open(os.path.join(out, name)) as f:
            assert f.read() == committed, f"{name} differs from scenes.py output"
        scene = json.loads(committed)
        if scene.get("type") == "ring":
            continue
        links = {frozenset((u, v)) for u, v, *_ in scene["links"]}
        for ev in scene["events"]:
            path = ev[3]
            for a, b in zip(path, path[1:]):
                assert frozenset((a, b)) in links, (name, a, b)


def utils_moe_messages():
    """msg-moe: every source sends S except the share for its own chip's
    experts (total S x (N - 1) when popularity is uniform); --combine
    doubles it with return messages that wait for their dispatch; skewed
    popularity makes the all-to-all slower on a Clos (hot chips)."""
    n, S = 8, 1_600_000
    out = os.path.join(TMP, "moe.txt")
    exporter("collectives.py", "msg-moe", out, "--bytes", str(S), "--endpoints", str(n))
    total = sum(int(l.split()[3]) for l in open(out) if not l.startswith("#"))
    assert abs(total - S * (n - 1)) / (S * n) < 0.001, total
    exporter("collectives.py", "msg-moe", out, "--bytes", str(S), "--endpoints", str(n),
             "--combine", "--skew", "1")
    lines = [l.split() for l in open(out) if not l.startswith("#")]
    comb = [l for l in lines if "tag=moe.combine" in l]
    assert len(comb) == len(lines) // 2 and all(any(x.startswith("after=") for x in l) for l in comb)
    d = os.path.join(TMP, "clos_moe")
    exporter("clos.py", d, "--chips", str(n), "--switches", "4")
    big = ["num_vcs=4", "vc_buf_size=512", "fabric_eject_any=1"]
    t = {}
    for z in ("0", "2"):
        exporter("collectives.py", "msg-moe", f"{d}/msgs.txt", "--bytes", "400000",
                 "--endpoints", str(n), "--experts-per-chip", "4", "--skew", z)
        t[z], _, _ = _messages(d, big)
    assert t["2"] > 2 * t["0"], t


TESTS = [make_upstream_test(c) for c in UPSTREAM] + [
    fabric_latency_per_hop, fabric_dor_torus_hops, fabric_src_torus_fixture,
    fabric_switched_terminals, fabric_groupshift, fabric_matrix_errors, fabric_port_gap_error,
    fabric_measure_reorder, utils_boardfly_collectives, utils_torus_fault_routes,
    utils_cdgcheck, utils_torus_twisted_hops, fabric_batch_paced,
    fabric_messages_basic, fabric_messages_endpoints, fabric_messages_stall,
    utils_collective_messages, fabric_messages_ring_allreduce,
    fabric_link_rates, fabric_stats_json, utils_harness,
    fabric_link_util, fabric_messages_stream,
    fabric_compute_terminals, fabric_eject_any, fabric_multipath,
    fabric_multicast_relay, utils_viewer_scenes,
    utils_moe_messages,
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
