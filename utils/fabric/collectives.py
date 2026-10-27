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

usage: collectives.py torus-rings OUT [--dims X Y Z] [--twisted]
       collectives.py boardfly OUT [--groups G] [--stage tray|group|global|fused]
       collectives.py rings OUT TOPO_FILE [--k K]
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
        if l[0] == "t":
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


def rings(topo, k):
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
    flows = {}
    for c in found:
        for i, a in enumerate(c):
            for b in (c[i - 1], c[(i + 1) % n]):
                flows[(a, b)] = flows.get((a, b), 0) + 1
    return n, flows, len(found)


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
    a = ap.parse_args()
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
