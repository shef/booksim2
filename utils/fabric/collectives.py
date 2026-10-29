#!/usr/bin/env python3
"""Traffic matrices for collectives (`traffic = matrix`, doc/fabric/formats.md).

Each bandwidth-bound phase of a collective is a steady endpoint-to-endpoint
matrix. Weights are bytes sent per endpoint for an all-reduce of S = 1 byte
per chip (the reduce-scatter half; the all-gather mirrors it). Run them with
several terminals per chip (fabric_terminals) so a chip can drive all its
links; time = bytes per chip / (accepted links per chip x link rate).

  torus-rings      three shards at once, each a bidirectional-ring
                   reduce-scatter over the dimensions in a rotated order
                   (X,Y,Z / Y,Z,X / Z,X,Y), so every link is busy. Uses
                   torus.py's chip numbering (x fastest); --twisted follows the
                   twisted wraps.
  boardfly         hierarchical reduce-scatter on a Boardfly slice: tray (3
                   peers, S/4 each), group (7 peers at the same tray position,
                   S/32 each), global (G-1 peers at the same chip index,
                   S/(32 G) each); one --stage, or `fused` for all three at
                   once (stages pipelined over shards).
  rings            bidirectional ring all-reduce over up to K edge-disjoint
                   Hamiltonian cycles found in a topology file (randomized
                   extension with Posa rotations); each chip sends equally to
                   its two neighbours on every ring.

Message files (`sim_type = messages`, doc/fabric/messages.md): the same
collectives as fixed messages with dependencies, timed to completion.
--bytes is the buffer per chip, as in NCCL: an all-reduce of S bytes, or an
all-to-all where each chip sends S / N to every other chip.

  msg-alltoall       every endpoint sends S / N to every other, all at once
  msg-torus          torus all-reduce: in each of three dimension orders
                     (X,Y,Z / Y,Z,X / Z,X,Y) and both ring directions, a ring
                     reduce-scatter along each dimension's lines in turn, then
                     the all-gather back; S split over the six. Rings follow
                     coordinate lines; on a twisted torus the wrap message is
                     routed like any other.
  msg-boardfly       hierarchical all-reduce on a Boardfly slice: direct
                     exchange within trays, then groups, then across groups
  msg-rings          bidirectional ring all-reduce over up to K edge-disjoint
                     Hamiltonian cycles of a topology file
  msg-ring           bidirectional ring all-reduce over the endpoints in order
                     (a logical ring routed by the fabric; for switched fabrics)
  msg-innet          in-network all-reduce: chips stream 1/K of the data to
                     each of K compute-terminal engines (--engines R,R,...,
                     written cR), which stream the reduced result back
  msg-tree           tree all-reduce over the endpoints: the whole buffer is
                     reduced up a binary tree and broadcast back down, chunk
                     by chunk (use --chunks to pipeline); --double splits it
                     over two complementary trees (NCCL's double binary tree)

  --chunks C splits the work into C independent pipelines; --reduce-gbps R
  adds the time to reduce received data (bytes / R) before forwarding it.

usage: collectives.py torus-rings OUT [--dims X Y Z] [--twisted]
       collectives.py boardfly OUT [--groups G] [--stage tray|group|global|fused]
       collectives.py rings OUT TOPO_FILE [--k K]
       collectives.py msg-alltoall OUT --bytes S (--endpoints N | --topo TOPO_FILE)
       collectives.py msg-torus OUT --bytes S [--dims X Y Z] [COMMON]
       collectives.py msg-boardfly OUT --bytes S [--groups G] [COMMON]
       collectives.py msg-rings OUT TOPO_FILE --bytes S [--k K] [COMMON]
       collectives.py msg-ring OUT --bytes S (--endpoints N | --topo TOPO_FILE) [COMMON]
       collectives.py msg-innet OUT --bytes S --endpoints N --engines R,R,... [COMMON]
       collectives.py msg-tree OUT --bytes S (--endpoints N | --topo TOPO_FILE)
                      [--double] [COMMON]
         COMMON: [--chunks C] [--reduce-gbps R]
"""

import argparse
import random

import fabricio
import torus


def torus_rings(D, twisted):
    twist = torus.twist_type(D) if twisted else 0
    w = [0.0] * 3
    for order in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        size = 1.0 / 3
        for d in order:
            w[d] += size * (D[d] - 1) / D[d]
            size /= D[d]
    N = D[0] * D[1] * D[2]
    flows = {}
    for i in range(N):
        p = torus.coord(i, D)
        for d in range(3):
            for sg in (0, 1):
                flows[(i, torus.index(torus.step(p, d, sg, D, twist), D))] = w[d] / 2
    return N, flows


def boardfly(groups, stage):
    flows = {}
    stages = ("tray", "group", "global") if stage == "fused" else (stage,)
    for st in stages:
        for g in range(groups):
            for i in range(32):
                src, t, c = g * 32 + i, i // 4, i % 4
                if st == "tray":
                    peers, b = [g * 32 + t * 4 + x for x in range(4) if x != c], 1 / 4
                elif st == "group":
                    peers, b = [g * 32 + x * 4 + c for x in range(8) if x != t], 1 / 32
                else:
                    peers, b = [h * 32 + i for h in range(groups) if h != g], 1 / (32 * groups)
                for p in peers:
                    flows[(src, p)] = flows.get((src, p), 0) + b
    return groups * 32, flows


def load_graph(path):
    with open(path) as f:
        lines = [l.split("#")[0].split() for l in f]
    lines = [l for l in lines if l]
    n = int(lines[0][0])
    adj = [set() for _ in range(n)]
    for l in lines[1:]:
        if l[0] in ("t", "c"):
            continue
        u, v = int(l[0]), int(l[2])
        adj[u].add(v)
        adj[v].add(u)
    return n, adj


def ham_cycle(n, adj, rng, tries=200, steps=200000):
    for _ in range(tries):
        start = rng.randrange(n)
        path, pos = [start], {start: 0}
        for _ in range(steps):
            end = path[-1]
            if len(path) == n and path[0] in adj[end]:
                return path
            free = [v for v in adj[end] if v not in pos]
            if free:
                # Warnsdorff: fewest free neighbours first.
                v = min(free, key=lambda x: (sum(1 for y in adj[x] if y not in pos), rng.random()))
                pos[v] = len(path)
                path.append(v)
                continue
            # Rotate: pick a neighbour on the path, reverse the tail after it.
            cands = [v for v in adj[end] if v != path[-2]] if len(path) > 1 else []
            if not cands:
                break
            i = pos[rng.choice(cands)]
            path[i + 1:] = path[i + 1:][::-1]
            for j in range(i + 1, len(path)):
                pos[path[j]] = j
    return None


def ham_cycles(topo, k):
    """Up to k edge-disjoint Hamiltonian cycles of a topology file."""
    n, adj = load_graph(topo)
    rng = random.Random(1)
    found = []
    for _ in range(k):
        c = ham_cycle(n, adj, rng)
        if c is None:
            break
        found.append(c)
        for a, b in zip(c, c[1:] + c[:1]):
            adj[a].discard(b)
            adj[b].discard(a)
    return n, found


def rings(topo, k):
    n, found = ham_cycles(topo, k)
    flows = {}
    for c in found:
        for i, a in enumerate(c):
            for b in (c[i - 1], c[(i + 1) % n]):
                flows[(a, b)] = flows.get((a, b), 0) + 1
    return n, flows, len(found)


# ---- Message schedules ----------------------------------------------------
#
# A collective is a list of stages; each stage splits the endpoints into
# groups that exchange data either around a ring (n - 1 steps to a
# neighbour) or directly (one step to every peer). An all-reduce runs the
# stages as a reduce-scatter, each leaving 1 / n of the data, then in
# reverse as an all-gather. Each endpoint's sends wait for the data they
# forward: a ring step for the previous step's incoming message, a stage's
# first sends for what the endpoint received in the previous stage's last
# step.


class Schedule:
    def __init__(self):
        self.msgs = []

    def add(self, src, dst, nbytes, deps=(), delay=0, tag="", stream=()):
        self.msgs.append((src, dst, max(1, round(nbytes)), sorted(set(deps)), delay, tag,
                          sorted(set(stream))))
        return len(self.msgs) - 1


def _stage(sch, groups, algo, shard, frontier, tag, reduce_ns_per_byte):
    """One stage; shard = bytes per message. Returns the new frontier
    {endpoint: message ids it received in the stage's last step}."""
    new = {}
    for g in groups:
        n = len(g)
        if n < 2:
            continue
        delay = round(shard * reduce_ns_per_byte)
        if algo == "ring":
            incoming = {m: frontier.get(m, []) for m in g}
            for k in range(n - 1):
                got = {}
                for i, m in enumerate(g):
                    dst = g[(i + 1) % n]
                    deps = incoming[m]
                    got[dst] = [sch.add(m, dst, shard, deps, delay if deps else 0,
                                        f"{tag}.{k}")]
                incoming = got
            new.update(incoming)
        else:
            got = {m: [] for m in g}
            for m in g:
                deps = frontier.get(m, [])
                for dst in g:
                    if dst != m:
                        got[dst].append(sch.add(m, dst, shard, deps, delay if deps else 0, tag))
            new.update(got)
    return new


def allreduce(sch, stages, nbytes, reduce_gbps=0, prefix=""):
    """stages: [(groups, "ring" | "direct")], groups of equal size per stage."""
    rns = 1.0 / reduce_gbps if reduce_gbps else 0.0  # GB/s = bytes per ns
    sizes, size = [], float(nbytes)
    for groups, _ in stages:
        sizes.append(size / len(groups[0]))      # shard per message
        size /= len(groups[0])
    frontier = {}
    for s, (groups, algo) in enumerate(stages):
        frontier = _stage(sch, groups, algo, sizes[s], frontier, f"{prefix}rs{s}", rns)
    for s in reversed(range(len(stages))):
        groups, algo = stages[s]
        frontier = _stage(sch, groups, algo, sizes[s], frontier, f"{prefix}ag{s}", 0.0)


def chunked(fn, chunks, nbytes, *args, **kw):
    """Run fn(schedule, ..., nbytes / chunks) once per chunk (independent
    pipelines sharing the network)."""
    sch = Schedule()
    for _ in range(chunks):
        fn(sch, *args, nbytes / chunks, **kw)
    return sch


def msg_alltoall(endpoints, nbytes):
    sch = Schedule()
    n = len(endpoints)
    for s in endpoints:
        for d in endpoints:
            if s != d:
                sch.add(s, d, nbytes / n, tag="a2a")
    return sch


def torus_lines(D, d):
    """Groups of chips along dimension d (coordinate lines, ring order)."""
    groups = []
    others = [k for k in range(3) if k != d]
    for a in range(D[others[0]]):
        for b in range(D[others[1]]):
            line = []
            for x in range(D[d]):
                p = [0, 0, 0]
                p[d], p[others[0]], p[others[1]] = x, a, b
                line.append(torus.index(p, D))
            groups.append(line)
    return groups


def msg_torus(sch, D, nbytes, reduce_gbps=0):
    for order in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        for direction in (1, -1):
            stages = [([g[::direction] for g in torus_lines(D, d)], "ring") for d in order]
            allreduce(sch, stages, nbytes / 6, reduce_gbps)


def msg_boardfly(sch, groups, nbytes, reduce_gbps=0):
    tray = [[g * 32 + t * 4 + c for c in range(4)] for g in range(groups) for t in range(8)]
    group = [[g * 32 + t * 4 + c for t in range(8)] for g in range(groups) for c in range(4)]
    glob = [[g * 32 + i for g in range(groups)] for i in range(32)]
    stages = [(tray, "direct"), (group, "direct")] + ([(glob, "direct")] if groups > 1 else [])
    allreduce(sch, stages, nbytes, reduce_gbps)


def msg_rings(sch, cycles, nbytes, reduce_gbps=0):
    for c in cycles:
        for direction in (1, -1):
            allreduce(sch, [([c[::direction]], "ring")], nbytes / (2 * len(cycles)), reduce_gbps)


def binary_tree(order):
    """parent of each endpoint in a binary tree over `order` (heap layout:
    order[0] is the root, order[i]'s parent is order[(i - 1) // 2])."""
    return {order[i]: (order[(i - 1) // 2] if i else None) for i in range(len(order))}


def msg_tree(sch, parent, nbytes, reduce_gbps=0, tag="tree"):
    """Reduce nbytes up the tree, then broadcast down. A node sends up once
    all its children's data has arrived (plus reduction time), and down once
    its parent's has; the root turns the reduction around."""
    children = {m: [] for m in parent}
    for m, p in parent.items():
        if p is not None:
            children[p].append(m)
    delay = round(nbytes / reduce_gbps) if reduce_gbps else 0
    up = {}                                   # node -> its message to the parent
    def send_up(m):
        for c in children[m]:
            send_up(c)
        deps = [up[c] for c in children[m]]
        if parent[m] is not None:
            up[m] = sch.add(m, parent[m], nbytes, deps, delay if deps else 0, f"{tag}.up")
    root = next(m for m, p in parent.items() if p is None)
    send_up(root)
    def send_down(m, dep):
        for c in children[m]:
            send_down(c, [sch.add(m, c, nbytes, dep, 0, f"{tag}.down")])
    send_down(root, [up[c] for c in children[root]])


def msg_trees(sch, endpoints, nbytes, double=False, reduce_gbps=0):
    if not double:
        msg_tree(sch, binary_tree(endpoints), nbytes, reduce_gbps)
        return
    # Complementary trees: the second runs over the reversed order, so the
    # first tree's leaves are (mostly) the second's inner nodes.
    msg_tree(sch, binary_tree(endpoints), nbytes / 2, reduce_gbps, "tree0")
    msg_tree(sch, binary_tree(endpoints[::-1]), nbytes / 2, reduce_gbps, "tree1")


def msg_ring(sch, endpoints, nbytes, reduce_gbps=0):
    """Bidirectional ring all-reduce over endpoints in the given order
    (a logical ring: each step's message is routed by the fabric)."""
    for direction in (1, -1):
        allreduce(sch, [([endpoints[::direction]], "ring")], nbytes / 2, reduce_gbps)


def msg_innet(sch, chips, engines, nbytes, reduce_gbps=0):
    """In-network all-reduce: each chip streams 1/K of its data to each of the
    K engines (compute terminals, "cR"), which reduce as data arrives and
    stream the result back to every chip, as switch-reduction (SHARP-style)
    collectives do."""
    k = len(engines)
    delay = round(nbytes / k / reduce_gbps / max(1, len(chips))) if reduce_gbps else 0
    for e in engines:
        ups = [sch.add(c, e, nbytes / k, tag="innet.up") for c in chips]
        for c in chips:
            sch.add(e, c, nbytes / k, delay=delay, tag="innet.down", stream=ups)


def endpoints_of(topo):
    """Routers with terminals in a topology file (all, unless `t R 0`)."""
    with open(topo) as f:
        lines = [l.split("#")[0].split() for l in f]
    lines = [l for l in lines if l]
    n = int(lines[0][0])
    none = {int(l[1]) for l in lines[1:] if l[0] == "t" and int(l[2]) == 0}
    return [r for r in range(n) if r not in none]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("torus-rings")
    t.add_argument("out")
    t.add_argument("--dims", type=int, nargs=3, default=[8, 8, 16])
    t.add_argument("--twisted", action="store_true")
    b = sub.add_parser("boardfly")
    b.add_argument("out")
    b.add_argument("--groups", type=int, default=32)
    b.add_argument("--stage", choices=["tray", "group", "global", "fused"], default="fused")
    r = sub.add_parser("rings")
    r.add_argument("out")
    r.add_argument("topo")
    r.add_argument("--k", type=int, default=3)
    m = {}
    for name in ("msg-alltoall", "msg-torus", "msg-boardfly", "msg-rings", "msg-tree",
                 "msg-ring", "msg-innet"):
        m[name] = sub.add_parser(name)
        m[name].add_argument("out")
        if name == "msg-rings":
            m[name].add_argument("topo")
            m[name].add_argument("--k", type=int, default=3)
        m[name].add_argument("--bytes", type=float, required=True)
        if name != "msg-alltoall":
            m[name].add_argument("--chunks", type=int, default=1)
            m[name].add_argument("--reduce-gbps", type=float, default=0)
    for name in ("msg-alltoall", "msg-tree", "msg-ring", "msg-innet"):
        m[name].add_argument("--endpoints", type=int)
        m[name].add_argument("--topo")
    m["msg-innet"].add_argument("--engines", required=True,
                                help="routers whose compute terminals reduce, e.g. 8,9,10,11")
    m["msg-tree"].add_argument("--double", action="store_true")
    m["msg-torus"].add_argument("--dims", type=int, nargs=3, default=[8, 8, 16])
    m["msg-boardfly"].add_argument("--groups", type=int, default=32)
    a = ap.parse_args()
    if a.cmd.startswith("msg-"):
        if a.cmd == "msg-alltoall":
            eps = endpoints_of(a.topo) if a.topo else list(range(a.endpoints))
            sch = msg_alltoall(eps, a.bytes)
        elif a.cmd == "msg-torus":
            sch = chunked(msg_torus, a.chunks, a.bytes, a.dims, reduce_gbps=a.reduce_gbps)
        elif a.cmd == "msg-ring":
            eps = endpoints_of(a.topo) if a.topo else list(range(a.endpoints))
            sch = chunked(msg_ring, a.chunks, a.bytes, eps, reduce_gbps=a.reduce_gbps)
        elif a.cmd == "msg-innet":
            eps = endpoints_of(a.topo) if a.topo else list(range(a.endpoints))
            engines = [f"c{r}" for r in a.engines.split(",")]
            sch = chunked(msg_innet, a.chunks, a.bytes, eps, engines, reduce_gbps=a.reduce_gbps)
        elif a.cmd == "msg-tree":
            eps = endpoints_of(a.topo) if a.topo else list(range(a.endpoints))
            sch = chunked(msg_trees, a.chunks, a.bytes, eps, double=a.double,
                          reduce_gbps=a.reduce_gbps)
        elif a.cmd == "msg-boardfly":
            sch = chunked(msg_boardfly, a.chunks, a.bytes, a.groups, reduce_gbps=a.reduce_gbps)
        else:
            _, cycles = ham_cycles(a.topo, a.k)
            print(f"{len(cycles)} edge-disjoint Hamiltonian rings")
            sch = chunked(msg_rings, a.chunks, a.bytes, cycles, reduce_gbps=a.reduce_gbps)
        fabricio.write_messages(a.out, sch.msgs)
        print(f"{a.out}: {len(sch.msgs)} messages, "
              f"{sum(m[2] for m in sch.msgs)} bytes")
        return
    if a.cmd == "torus-rings":
        n, flows = torus_rings(a.dims, a.twisted)
    elif a.cmd == "boardfly":
        n, flows = boardfly(a.groups, a.stage)
    else:
        n, flows, found = rings(a.topo, a.k)
        print(f"{found} edge-disjoint Hamiltonian rings")
    fabricio.write_matrix(a.out, n, flows)
    print(f"{a.out}: {n} endpoints, {len(flows)} flows, "
          f"{sum(flows.values()) / n:.4f} sent per endpoint")


if __name__ == "__main__":
    main()
