#!/usr/bin/env python3
"""Check source routes for deadlock freedom (doc/fabric/routing.md, `src`).

Builds the channel dependency graph of a path file: a channel is (router,
output port, VC), and a route that leaves a router on channel a and the next
router on channel b adds the edge a -> b. With wormhole switching the routes
are deadlock-free if this graph has no cycle (Dally and Seitz, 1987). The
`src` routing function does not check this itself.

Prints the number of channels and dependencies, and either "acyclic" or one
cycle as a list of channels; exits 1 on a cycle.

usage: cdgcheck.py TOPO_FILE PATH_FILE
"""

import argparse
import sys


def load_links(path):
    """(router, port) -> neighbouring router, from a fabric topology file."""
    nxt = {}
    with open(path) as f:
        lines = [l.split("#")[0].split() for l in f]
    for l in lines[1:]:
        if not l or l[0] == "t":
            continue
        u, pu, v, pv = map(int, l[:4])
        nxt[(u, pu)] = v
        nxt[(v, pv)] = u
    return nxt


def dependencies(nxt, path_file):
    deps = {}
    with open(path_file) as f:
        for line in f:
            x = list(map(int, line.split()))
            if not x:
                continue
            s, n, hops = x[0], x[2], x[3:]
            r, prev = s, None
            for i in range(n):
                p, v = hops[2 * i], hops[2 * i + 1]
                ch = (r, p, v)
                deps.setdefault(ch, set())
                if prev is not None:
                    deps[prev].add(ch)
                prev, r = ch, nxt[(r, p)]
    return deps


def find_cycle(deps):
    """Iterative DFS; returns one cycle as a list of channels, or None."""
    color, parent = {}, {}
    for root in deps:
        if root in color:
            continue
        stack = [(root, iter(deps[root]))]
        color[root] = 1
        while stack:
            u, it = stack[-1]
            for v in it:
                if color.get(v, 0) == 0:
                    color[v], parent[v] = 1, u
                    stack.append((v, iter(deps.get(v, ()))))
                    break
                if color[v] == 1:
                    cyc, w = [v], u
                    while w != v:
                        cyc.append(w)
                        w = parent[w]
                    return cyc[::-1]
            else:
                color[u] = 2
                stack.pop()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topo")
    ap.add_argument("paths")
    a = ap.parse_args()
    deps = dependencies(load_links(a.topo), a.paths)
    n_edges = sum(len(v) for v in deps.values())
    cyc = find_cycle(deps)
    print(f"{len(deps)} channels, {n_edges} dependencies: " +
          ("acyclic" if cyc is None else f"cycle of {len(cyc)} channels"))
    if cyc:
        print(" -> ".join(f"r{r}:p{p}:vc{v}" for r, p, v in cyc[:12]) + (" ..." if len(cyc) > 12 else ""))
        sys.exit(1)


if __name__ == "__main__":
    main()
