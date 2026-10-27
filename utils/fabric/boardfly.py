#!/usr/bin/env python3
"""Export a TPU 8i Boardfly slice for `topology = fabric`, optionally with
failures, with tables for the `lduato` routing function (doc/systems/tpu-boardfly.md).

Structure: trays of 4 fully connected chips; groups of 8 trays fully
connected over copper (2 copper links per chip, one tray pair doubled); one
fiber per chip to the OCS, so 32 global ports per group.

A slice is N groups (N in 1, 2, 4, 8, 16, 32) or, for N = 1, the first
`--chips` chips of one group (4, 8, 16 or 32). The OCS spreads each group's
32 global ports over the other N - 1 groups of the slice: with N = 32 that is
the usual full mesh (one link per group pair, port 31 spare); with fewer groups
each pair gets 32 / (N - 1) parallel links. Port r always connects to port r
of the peer group, so both ends sit on the same OCS plane (`--planes 2`:
A for r < 16, B for r >= 16; `--planes 4`: A-D in blocks of 8).

Failures remove physical parts:
  chip / tray    the chip (or the tray's four chips, e.g. a host failure) and all their links
  loop           an in-tray loop cable (chips 0-3 or 1-2)
  copper         an inter-tray copper cable
  optic          a chip's optical transceiver: its global link is gone
  ocs A..D       a whole OCS plane: every global link on it is gone
--repair re-aims the OCS: a lost global link is restored between free, healthy
ports of the same two groups on the same plane (the orphaned peer port or the
spare port 31); with a plane down, the surviving planes are re-planned.

Routing (Duato, layered escape):
  adaptive  VCs 0..n-L-1 on every next hop of a minimal path, where minimal
            means fewest global hops, then fewest intra-group hops
  escape    a fixed group path (BFS), up*/down* inside each group toward the
            exit chip, then the global link; escape VC = adaptive + layer, with
            layer = Lmax - (group hops left), so the layer only grows. L = Lmax + 1
            escape VCs (fabric_esc_layers): 2 when every group pair has a direct
            link, 3 when some pair needs a transit group.

Writes topo.txt and tables.txt (doc/fabric/formats.md) and meta.json (the
slice, failures and L). --verify checks that the escape channel dependency
graph is acyclic.

usage: boardfly.py OUTDIR [--groups N] [--ocs A] [--repair] [--verify] ...
"""

import argparse
import collections
import heapq
import json
import os
import random

import fabricio

# Calibration (doc/systems/tpu-boardfly.md): per-hop latency in ns, including
# SerDes and FEC. Fiber: 80 ns of electronics plus 5 ns/m over TPU -> OCS -> TPU.
LAT_TRAY = 60
LAT_COPPER = 90
ELECTRONICS_NS = 80
FIBER_NS_PER_M = 5
OCS_DISTANCE_M = 100


def fiber_latency(distance_m):
    return round(ELECTRONICS_NS + FIBER_NS_PER_M * 2 * distance_m)


G = 32  # chips per group
TRAY = 4  # chips per tray
TRAYS = G // TRAY
EXTRA_TRAY_PAIRS = [(0, 1), (2, 3), (4, 5), (6, 7)]  # doubled copper links
LAT = {"T": LAT_TRAY, "C": LAT_COPPER, "G": fiber_latency(OCS_DISTANCE_M)}
KIND = {"T": 0, "C": 1, "G": 2}
LOOPS = [(0, 3), (1, 2)]  # in-tray pairs joined by the short loop cable
COST = {"T": 1, "C": 1, "G": 1000}  # minimal = fewest global hops, then fewest hops
PLANES = 2  # OCS planes; group port r sits on plane r // (32 / PLANES)


def chip_id(g, t, c):
    return g * G + t * TRAY + c


def tray_edges():
    """Inter-tray copper edges inside a group: K8 plus the parallel pairs."""
    edges = [(a, b, 0) for a in range(TRAYS) for b in range(a + 1, TRAYS)]
    edges += [(a, b, 1) for a, b in EXTRA_TRAY_PAIRS]
    return edges


def assign_tray_ports(parallel):
    """Map each inter-tray edge end to a chip, two copper ports per chip.

    parallel='same'   -> both links of a doubled pair land on the same chips
    parallel='spread' -> the two links land on different chips
    """
    edges = tray_edges()
    incident = collections.defaultdict(list)
    for e in edges:
        a, b, k = e
        incident[a].append((b, k, e))
        incident[b].append((a, k, e))
    ends = {}
    for t, lst in incident.items():
        if parallel == "same":
            lst.sort(key=lambda x: (x[0], x[1]))
        else:
            lst.sort(key=lambda x: (x[1], x[0]))
        assert len(lst) == 2 * TRAY
        for i, (_, _, e) in enumerate(lst):
            ends[(e, t)] = i // 2
    return edges, ends


def round_robin_matchings(n):
    """Circle-method 1-factorization of K_n: n-1 rounds of n/2 disjoint pairs."""
    players = list(range(n))
    rounds = []
    for _ in range(n - 1):
        rounds.append([(players[i], players[n - 1 - i]) for i in range(n // 2)])
        players = [players[0]] + [players[-1]] + players[1:-1]
    return rounds


def plane(r):
    return "ABCD"[r // (G // PLANES)]


def build(groups, chips=G):
    """Physical slice: chips, typed links, global port wiring."""
    assert groups in (1, 2, 4, 8, 16, 32) and (groups == 1 or chips == G)
    trays = chips // TRAY
    links = []  # dict(u, v, kind, part)
    for g in range(groups):
        for t in range(trays):
            for c1 in range(TRAY):
                for c2 in range(c1 + 1, TRAY):
                    part = ("loop", g, t, LOOPS.index((c1, c2))) if (c1, c2) in LOOPS else None
                    links.append(dict(u=chip_id(g, t, c1), v=chip_id(g, t, c2), kind="T", part=part))
        edges, ends = assign_tray_ports("same")
        for i, e in enumerate(edges):
            ta, tb, _ = e
            if ta < trays and tb < trays:
                links.append(dict(u=chip_id(g, ta, ends[(e, ta)]), v=chip_id(g, tb, ends[(e, tb)]),
                                  kind="C", part=("copper", g, i)))
    gports = []  # (g, r, h): port r of group g wired to port r of group h
    if groups > 1:
        rounds = round_robin_matchings(groups)
        nports = G - 1 if groups == 32 else G
        for r in range(nports):
            for g, h in rounds[r % (groups - 1)]:
                gports.append((g, r, h))
    return dict(groups=groups, chips=chips, links=links, gports=gports, n=groups * G)


def present(sl):
    """Chip ids that exist in the slice (sub-group slices use a prefix)."""
    return [g * G + c for g in range(sl["groups"]) for c in range(sl["chips"])]


def sample_failures(sl, args, rng):
    fails = []
    chips = present(sl)
    if args.ocs:
        fails.append(("ocs", args.ocs))
    for _ in range(args.chip):
        fails.append(("chip", rng.choice(chips)))
    for _ in range(args.tray):
        fails.append(("tray", rng.randrange(sl["groups"]), rng.randrange(sl["chips"] // TRAY)))
    for kind, k in (("loop", args.loop), ("copper", args.copper)):
        parts = [l["part"] for l in sl["links"] if l["part"] and l["part"][0] == kind]
        fails += [p for p in rng.sample(parts, min(k, len(parts)))]
    used = sorted({(g, r) for g, r, _ in sl["gports"]} | {(h, r) for _, r, h in sl["gports"]})
    fails += [("optic", g, r) for g, r in rng.sample(used, min(args.optic, len(used)))]
    return fails


def apply(sl, fails, repair):
    dead_chips, dead_parts, dead_ports, down_planes = set(), set(), set(), set()
    for f in fails:
        if f[0] == "chip":
            dead_chips.add(f[1])
        elif f[0] == "tray":
            dead_chips.update(chip_id(f[1], f[2], c) for c in range(TRAY))
        elif f[0] in ("loop", "copper"):
            dead_parts.add(tuple(f))
        elif f[0] == "optic":
            dead_ports.add((f[1], f[2]))
        elif f[0] == "ocs":
            down_planes.add(f[1])

    def port_ok(g, r):
        return (g, r) not in dead_ports and plane(r) not in down_planes and g * G + r not in dead_chips

    links = [dict(l) for l in sl["links"]
             if l["u"] not in dead_chips and l["v"] not in dead_chips and l["part"] not in dead_parts]
    gports = sl["gports"]
    if repair and down_planes and sl["groups"] == 32 and len(down_planes) == 1:
        # Re-plan the surviving planes: their ports (spare included) take rounds
        # of the 1-factorization spread evenly over all 31 (every other round
        # with two planes), which keeps the group graph at diameter 2.
        ok_ports = [r for r in range(G) if plane(r) not in down_planes]
        rounds = round_robin_matchings(32)
        gports = [(g, r, h) for k, r in enumerate(ok_ports)
                  for g, h in rounds[k * 31 // len(ok_ports)]]
        gports += [(g, r, h) for g, r, h in sl["gports"] if plane(r) in down_planes]
    used, lost = set(), []
    for g, r, h in gports:
        if port_ok(g, r) and port_ok(h, r):
            links.append(dict(u=g * G + r, v=h * G + r, kind="G", part=("glink", g, h, r)))
            used |= {(g, r), (h, r)}
        else:
            lost.append((g, h, r))
    repaired = 0
    if repair:
        for g, h, r in lost:
            free = lambda grp: [p for p in range(G) if port_ok(grp, p) and (grp, p) not in used]
            # Prefer the orphaned ports (r), then any free port; planes must match.
            cand = [(pg, ph) for pg in sorted(free(g), key=lambda p: p != r)
                    for ph in sorted(free(h), key=lambda p: p != r) if plane(pg) == plane(ph)]
            if cand:
                pg, ph = cand[0]
                links.append(dict(u=g * G + pg, v=h * G + ph, kind="G", part=("glink", g, h, pg)))
                used |= {(g, pg), (h, ph)}
                repaired += 1
    alive = [c for c in present(sl) if c not in dead_chips]
    return links, alive, dict(lost_global=len(lost), repaired=repaired, dead_chips=len(set(alive) ^ set(present(sl))))


def tables(links, alive):
    idx = {c: i for i, c in enumerate(alive)}
    R = len(alive)
    grp = [c // G for c in alive]
    adj = [[] for _ in range(R)]  # (nbr, port, kind)
    topo = []
    for l in links:
        u, v = idx[l["u"]], idx[l["v"]]
        pu, pv = len(adj[u]) + 1, len(adj[v]) + 1
        adj[u].append((v, pu, l["kind"]))
        adj[v].append((u, pv, l["kind"]))
        topo.append((u, pu, v, pv, l["kind"]))

    # Minimal-path distances to every destination (lexicographic cost).
    dist = []
    for d in range(R):
        dd = [1 << 60] * R
        dd[d] = 0
        pq = [(0, d)]
        while pq:
            c, u = heapq.heappop(pq)
            if c > dd[u]:
                continue
            for v, _, k in adj[u]:
                nc = c + COST[k]
                if nc < dd[v]:
                    dd[v] = nc
                    heapq.heappush(pq, (nc, v))
        assert max(dd) < 1 << 60, "slice is disconnected"
        dist.append(dd)

    # Group graph and the deterministic escape group path.
    groups = sorted(set(grp))
    gl = collections.defaultdict(list)  # (g, h) -> [(u, port)] exits from g to h
    for u in range(R):
        for v, p, k in adj[u]:
            if k == "G":
                gl[(grp[u], grp[v])].append((u, p))
    for key in gl:
        gl[key].sort()
    gnbr = {g: sorted({h for (a, h) in gl if a == g}) for g in groups}
    gd = {}
    for h in groups:
        dd = {h: 0}
        q = collections.deque([h])
        while q:
            x = q.popleft()
            for y in gnbr[x]:
                if y not in dd:
                    dd[y] = dd[x] + 1
                    q.append(y)
        assert len(dd) == len(groups), "group graph is disconnected"
        for g in groups:
            gd[(g, h)] = dd[g]
    lmax = max(gd.values())

    def exit_of(g, h):
        n = next(x for x in gnbr[g] if gd[(x, h)] == gd[(g, h)] - 1)
        cands = gl[(g, n)]
        return cands[h % len(cands)]

    # Up*/down* inside each group: BFS tree from the lowest chip id.
    rank = [None] * R
    for g in groups:
        members = [u for u in range(R) if grp[u] == g]
        lvl = {members[0]: 0}
        q = collections.deque([members[0]])
        while q:
            x = q.popleft()
            for y, _, k in adj[x]:
                if k != "G" and y not in lvl:
                    lvl[y] = lvl[x] + 1
                    q.append(y)
        assert len(lvl) == len(members), f"group {g} is internally disconnected"
        for u in members:
            rank[u] = (lvl[u], u)

    ud_cache = {}

    def ud_dist(t):
        """Distances to t over states (node, phase); phase 1 = already went down."""
        if t in ud_cache:
            return ud_cache[t]
        dist_s = {(t, 0): 0, (t, 1): 0}
        q = collections.deque(dist_s)
        while q:
            v, ph = q.popleft()
            for a, _, k in adj[v]:
                if k == "G":
                    continue
                up = rank[v] < rank[a]  # hop a -> v goes up
                if ph == 0 and not up:
                    continue
                if ph == 1 and up:
                    continue
                for st in ([(a, 0)] if up else [(a, 0), (a, 1)]):
                    if st not in dist_s:
                        dist_s[st] = dist_s[(v, ph)] + 1
                        q.append(st)
        ud_cache[t] = dist_s
        return dist_s

    def ud_next(u, t, ph):
        ds = ud_dist(t)
        if (u, ph) not in ds:
            return None
        for v, p, k in adj[u]:
            if k == "G":
                continue
            up = rank[v] < rank[u]
            if up and ph == 1:
                continue
            if ds.get((v, 0 if up else 1), 1 << 30) == ds[(u, ph)] - 1:
                return p
        raise AssertionError((u, t, ph))

    rows = []
    for u in range(R):
        for d in range(R):
            if u == d:
                rows.append((-1, -1, -1, 0, 0))
                continue
            g, h = grp[u], grp[d]
            mask = 0
            for v, p, k in adj[u]:
                if dist[d][v] + COST[k] == dist[d][u]:
                    mask |= 1 << p
            mn = (mask & -mask).bit_length() - 1
            layer = lmax - gd[(g, h)]
            if g == h:
                t = d
            else:
                t, gp = exit_of(g, h)
                if t == u:
                    rows.append((mn, gp, gp, mask, layer))
                    continue
            ud0 = ud_next(u, t, 0)
            ud1 = ud_next(u, t, 1)
            rows.append((mn, ud0, ud0 if ud1 is None else ud1, mask, layer))
    arrdown = []
    for u in range(R):
        bits = 0
        for v, p, k in adj[u]:
            if k != "G" and rank[u] > rank[v]:
                bits |= 1 << p
        arrdown.append(bits)
    return dict(R=R, adj=adj, topo=topo, rows=rows, arrdown=arrdown, grp=grp, lmax=lmax)


def verify(t):
    """Walk every escape path and check the escape channel dependency graph
    (channels = (router, port, layer)) is acyclic."""
    R, adj, rows, arrdown = t["R"], t["adj"], t["rows"], t["arrdown"]
    nbr = [{p: (v, k) for v, p, k in adj[u]} for u in range(R)]
    rev = {}
    for u, pu, v, pv, _ in t["topo"]:
        rev[(u, pu)], rev[(v, pv)] = pv, pu
    edges = collections.defaultdict(set)
    for d in range(R):
        for s in range(R):
            u, ph, prev, hops = s, 0, None, 0
            while u != d:
                row = rows[u * R + d]
                p = row[2] if ph else row[1]
                ch = (u, p, row[4])
                if prev is not None:
                    edges[prev].add(ch)
                v, k = nbr[u][p]
                # Arrival phase at v: 1 if u -> v went down the tree.
                ph = 0 if k == "G" else arrdown[v] >> rev[(u, p)] & 1
                prev, u = ch, v
                hops += 1
                assert hops < 64, ("escape loop", s, d)
    # Kahn's algorithm on the dependency graph.
    indeg = collections.Counter()
    nodes = set(edges)
    for a, bs in edges.items():
        nodes |= bs
        for x in bs:
            indeg[x] += 1
    q = collections.deque(n for n in nodes if indeg[n] == 0)
    done = 0
    while q:
        n = q.popleft()
        done += 1
        for x in edges.get(n, ()):
            indeg[x] -= 1
            if indeg[x] == 0:
                q.append(x)
    return done == len(nodes)


def write(t, outdir, meta):
    os.makedirs(outdir, exist_ok=True)
    fabricio.write_topology(os.path.join(outdir, "topo.txt"), t["R"],
                            [(u, pu, v, pv, KIND[k], LAT[k]) for u, pu, v, pv, k in t["topo"]])
    fabricio.write_tables(os.path.join(outdir, "tables.txt"), t["rows"], t["arrdown"])
    with open(os.path.join(outdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--groups", type=int, default=32)
    ap.add_argument("--chips", type=int, default=G)
    ap.add_argument("--optic", type=int, default=0)
    ap.add_argument("--copper", type=int, default=0)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--chip", type=int, default=0)
    ap.add_argument("--tray", type=int, default=0)
    ap.add_argument("--ocs", choices=list("ABCD"))
    ap.add_argument("--planes", type=int, default=2, choices=[2, 4])
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ocs-distance", type=float, default=OCS_DISTANCE_M,
                    help="TPU-to-OCS fiber length in metres (default %(default)s)")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    LAT["G"] = fiber_latency(a.ocs_distance)
    global PLANES
    PLANES = a.planes
    sl = build(a.groups, a.chips)
    fails = sample_failures(sl, a, random.Random(a.seed))
    links, alive, info = apply(sl, fails, a.repair)
    t = tables(links, alive)
    meta = dict({k: v for k, v in vars(a).items() if k != "outdir"}, failures=[list(f) for f in fails], routers=t["R"], lmax=t["lmax"],
                escape_vcs=t["lmax"] + 1, **info)
    if a.verify:
        meta["escape_acyclic"] = verify(t)
    write(t, a.outdir, meta)
    print(json.dumps({k: meta[k] for k in ("routers", "lmax", "escape_vcs", "lost_global", "repaired",
                                           "dead_chips") + (("escape_acyclic",) if a.verify else ())}))


if __name__ == "__main__":
    main()
