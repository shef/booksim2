#!/usr/bin/env python3
"""Export a single-tier Clos fabric for `topology = fabric` with `src` routes:
N chips and K switches, each chip with one link to every switch (the shape
of NVLink-switch systems). Generic: the latency and rate are parameters, not
a calibrated product model.

Routers 0..N-1 are chips with --terminals terminals each (default K, so a
chip can drive all its links); routers N..N+K-1 are switches. --compute T
gives every switch T compute terminals (an in-switch reduce and replicate
engine, `cR` in message files; doc/fabric/messages.md).

Ports: chip i's port k+1 links to switch k's port i+1.
Routes: chip i to chip j through switch (i + j) mod K, so all-to-all spreads
evenly, or with --multipath through every switch (one path line per switch;
each packet takes one, so a single flow uses all K links); chips to and from
a switch directly. VCs: packets to chip j use VC
j mod --vcs on both hops, so a switch input queues packets by destination
(approximating the virtual output queues real switches use) instead of one
FIFO where a packet waiting for a busy output blocks those behind it. Any VC
assignment is deadlock-free here: a two-hop path through a switch cannot
form a channel dependency cycle. Run with num_vcs >= --vcs.

usage: clos.py OUTDIR [--chips N] [--switches K] [--terminals T] [--vcs V]
               [--multipath] [--latency CYCLES] [--rate R] [--compute T]
"""

import argparse
import os

import fabricio


def build(n, k, latency, rate, vcs=1, multipath=False):
    links = []
    for i in range(n):
        for s in range(k):
            links.append((i, s + 1, n + s, i + 1, 0, latency, rate))
    routes = []
    for i in range(n):
        for j in range(n):
            if i != j:
                for s in (range(k) if multipath else [(i + j) % k]):
                    routes.append((i, j, [(s + 1, j % vcs), (j + 1, j % vcs)]))
        for s in range(k):
            routes.append((i, n + s, [(s + 1, 0)]))
            routes.append((n + s, i, [(i + 1, i % vcs)]))
    return links, routes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--chips", type=int, default=8)
    ap.add_argument("--switches", type=int, default=4)
    ap.add_argument("--terminals", type=int, help="terminals per chip (default: switches)")
    ap.add_argument("--latency", type=int, default=50, help="cycles per link")
    ap.add_argument("--rate", type=float, default=1.0, help="flits per cycle per link")
    ap.add_argument("--compute", type=int, default=0, help="compute terminals per switch")
    ap.add_argument("--vcs", type=int, default=4, help="VCs routes spread destinations over")
    ap.add_argument("--multipath", action="store_true", help="a path through every switch per pair")
    a = ap.parse_args()
    n, k = a.chips, a.switches
    links, routes = build(n, k, a.latency, a.rate, a.vcs, a.multipath)
    terms = {r: a.terminals or k for r in range(n)}
    terms.update({n + s: 0 for s in range(k)})
    os.makedirs(a.outdir, exist_ok=True)
    fabricio.write_topology(os.path.join(a.outdir, "topo.txt"), n + k, links, terms,
                            compute={n + s: a.compute for s in range(k)} if a.compute else None)
    fabricio.write_paths(os.path.join(a.outdir, "paths.txt"), routes)
    print(f"{a.outdir}: {n} chips, {k} switches, {len(links)} links"
          + (f", {a.compute} compute terminals per switch (c{n}..c{n + k - 1})" if a.compute else ""))


if __name__ == "__main__":
    main()
