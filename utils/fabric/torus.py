#!/usr/bin/env python3
"""Export a TPU 3D torus slice for `topology = fabric` with source routes for
`src` routing (doc/systems/tpu-torus.md).

Built from 4x4x4 copper cubes: links inside a cube are copper, links that
leave a cube (including wraparounds) go through the OCS. Plain or twisted
(`--twisted`, k x k x 2k or k x 2k x 2k): a wrap in a short dimension shifts
the long dimension(s) by k.

Routes:
  fault-free  dimension-order, longest dimension first (see dim_order for
              the order under a fault), shortest direction,
              ties broken by the source coordinate's parity (even -> positive),
              as in NSDI 2024. On a twisted torus the X and Y directions are
              chosen to minimize total hops given the shift their wraps cause.
  OCS fault   `--ocs-fault DIM`: one OCS failure in that dimension removes the
              links at the scenario's face positions on every cube face
              crossing (`--scenario`, positions mod 4 in the face's other two
              coordinates):
                one       (0, 0)          one face position per OCS
                adjacent  (0, 0), (1, 0)  a box holding two neighbouring positions
                spread    (0, 0), (2, 2)  a box holding two distant positions
              Routes follow NSDI 2024's method: dimension-order routing preceded
              by at most one "wild" hop per dimension (taken in reverse dimension
              order), one path per pair, the set chosen to minimize the busiest
              link under all-to-all traffic (rip-up and reroute standing in for
              NSDI's ILP), computed for one cube's sources and translated.
VCs: dimension-order hops use 0 on entering a dimension and 1 from that
dimension's wraparound hop on, which is deadlock-free (NSDI 2024). Wild hops
(`--vcs`): with 2 VCs they use VC 1, or VC 0 if they cross a wraparound; with
3 they use VC 2. NSDI 2024 says wild-first routing is deadlock-free with 2 VCs
given the ICI switch microarchitecture, which is not published; in a plain
wormhole router the 2-VC rule leaves the channel dependency graph acyclic
only on a single 4x4x4 cube among the cases we checked, while 3 VCs always
do. The exporter checks the graph (as cdgcheck.py does) and reports it.

usage: torus.py OUTDIR [--dims X Y Z] [--twisted] [--ocs-fault x|y|z]
                [--scenario one|adjacent|spread] [--vcs 2|3] [--ocs-distance M]
"""

import argparse
import collections
import itertools
import os

import cdgcheck
import fabricio

# Calibration (doc/systems/tpu-torus.md): per-hop latency in ns, including
# SerDes and FEC. Fiber: 80 ns of electronics plus 5 ns/m over TPU -> OCS -> TPU.
LAT_COPPER = 90
ELECTRONICS_NS = 80
FIBER_NS_PER_M = 5
OCS_DISTANCE_M = 100
CUBE = 4
SCENARIOS = {"one": [(0, 0)], "adjacent": [(0, 0), (1, 0)], "spread": [(0, 0), (2, 2)]}
fault_dim = None  # set by main from --ocs-fault


def fiber_latency(distance_m):
    return round(ELECTRONICS_NS + FIBER_NS_PER_M * 2 * distance_m)


def coord(i, D):
    return (i % D[0], i // D[0] % D[1], i // (D[0] * D[1]))


def index(p, D):
    return p[0] + D[0] * (p[1] + D[1] * p[2])


def step(p, dim, sign, D, twist):
    """One hop; sign 0 is +, 1 is -. Twist 1 (k x k x 2k): a wrap in a short
    dimension shifts the long one by k. Twist 2 (k x 2k x 2k): a wrap in the
    short dimension shifts both long ones by k."""
    p = list(p)
    d = -1 if sign else 1
    q = p[dim] + d
    if twist and (q < 0 or q >= D[dim]):
        k = min(D)
        for j in range(3):
            if j != dim and D[j] > D[dim] and (twist == 2 or D[j] == max(D)):
                p[j] = (p[j] + d * k) % D[j]
    p[dim] = q % D[dim]
    return tuple(p)


def twist_type(D):
    s = sorted(D)
    if s[2] == 2 * s[0] and s[1] == s[0]:
        return 1
    if s[1] == s[2] == 2 * s[0]:
        return 2
    raise SystemExit(f"--twisted needs k x k x 2k or k x 2k x 2k, not {D}")


def dim_order(D, twist):
    """Dimension routing order: longest first; on a twisted torus the short
    dimensions, whose wraps shift the long ones, go last. A failed dimension
    whose ring crosses two cube faces is never last: one OCS cuts both
    crossings in a column, splitting the ring, and a path's last leg cannot
    leave the destination's column, so some pairs would have no route.
    Among equal lengths it goes first; if it is still last, it moves first."""
    if twist:
        short = [k for k in range(3) if D[k] == min(D)]
        order = sorted((k for k in range(3) if k not in short), key=lambda k: -D[k])
        order += sorted(short, key=lambda k: k != fault_dim)
    else:
        order = sorted(range(3), key=lambda k: (-D[k], k != fault_dim))
    if fault_dim == order[-1] and D[fault_dim] > CUBE:
        order = [fault_dim] + order[:-1]
    return order


def dor_hops(s, d, D, twist, longway=()):
    """Dimension-order hops; on a plain torus, dimensions in `longway` go the
    other way around their ring."""
    if not twist:
        hops = []
        for dim in dim_order(D, 0):
            n, a, b = D[dim], s[dim], d[dim]
            delta = (b - a) % n
            if delta == 0:
                continue
            plus = 2 * delta < n or (2 * delta == n and a % 2 == 0)
            if plus != (dim in longway):
                hops += [(dim, 0)] * delta
            else:
                hops += [(dim, 1)] * (n - delta)
        return hops
    # Twisted: try both directions in each short dimension (its wraps shift
    # the long ones), then route the long dimensions shortest from where the
    # shifts leave them; keep the fewest hops. Long dimensions go first on the
    # path (shifts commute, so the end point is the same).
    order = dim_order(D, twist)
    shifters = [k for k in order if D[k] == min(D)]
    rest = [k for k in order if k not in shifters]
    best = None
    opts = []
    for dim in shifters:
        n, delta = D[dim], (d[dim] - s[dim]) % D[dim]
        opts.append([(0, delta)] if delta == 0 else [(0, delta), (1, n - delta)])
    for choice in itertools.product(*opts):
        hops = []
        for dim, (sg, cnt) in zip(shifters, choice):
            hops += [(dim, sg)] * cnt
        p = s
        for dim, sg in hops:
            p = step(p, dim, sg, D, twist)
        lh = []
        for dim in rest:
            n, delta = D[dim], (d[dim] - p[dim]) % D[dim]
            if 2 * delta < n or (2 * delta == n and s[dim] % 2 == 0):
                lh += [(dim, 0)] * delta
            else:
                lh += [(dim, 1)] * (n - delta)
        cand = lh + hops
        if best is None or len(cand) < len(best):
            best = cand
    return best


def faults_for(scenario, D, fdim, twist):
    others = [i for i in range(3) if i != fdim]
    bad = set()
    for x in range(D[fdim]):
        if x % CUBE != CUBE - 1:
            continue
        for a in range(D[others[0]]):
            for b in range(D[others[1]]):
                if (a % CUBE, b % CUBE) not in SCENARIOS[scenario]:
                    continue
                p = [0, 0, 0]
                p[fdim], p[others[0]], p[others[1]] = x, a, b
                p = tuple(p)
                q = step(p, fdim, 0, D, twist)
                bad |= {(p, q), (q, p)}
    return bad


def dor_variants(s, d, D, twist):
    """Every dimension-order path from s to d: each dimension may go either
    way round its ring (on a twisted torus, the short dimensions' choice
    shifts the long ones). All keep the dimension order, so the dateline VCs
    stay deadlock-free."""
    if not twist:
        for r in range(4):
            for lw in itertools.combinations(range(3), r):
                yield dor_hops(s, d, D, 0, lw)
        return
    order = dim_order(D, twist)
    shifters = [k for k in order if D[k] == min(D)]
    rest = [k for k in order if k not in shifters]
    opts = []
    for dim in shifters:
        n, delta = D[dim], (d[dim] - s[dim]) % D[dim]
        opts.append([(0, delta)] if delta == 0 else [(0, delta), (1, n - delta)])
    for choice in itertools.product(*opts):
        hops = []
        for dim, (sg, cnt) in zip(shifters, choice):
            hops += [(dim, sg)] * cnt
        p = s
        for dim, sg in hops:
            p = step(p, dim, sg, D, twist)
        for signs in itertools.product((0, 1), repeat=len(rest)):
            lh = []
            for dim, sg in zip(rest, signs):
                n, delta = D[dim], (d[dim] - p[dim]) % D[dim]
                if delta:
                    lh += [(dim, sg)] * (delta if sg == 0 else n - delta)
            yield lh + hops


def walk(s, hops, D, twist, bad):
    p = s
    for dim, sg in hops:
        q = step(p, dim, sg, D, twist)
        if (p, q) in bad:
            return None
        p = q
    return p


def candidates(s, d, D, twist, bad):
    """DOR, and DOR preceded by one wild hop in each subset of dimensions;
    if none survive, the same with dimension-order tails that may go either
    way round each ring (dor_variants); a shortest path avoiding failures if
    still none survive. Returns (hops, wild hops at the start), -1 for that last
    fallback, which can break deadlock freedom."""
    order = dim_order(D, twist)
    out = []
    for stage in ("shortest", "any direction"):
        for r in range(3):
            for dims in itertools.combinations(order, r):
                for signs in itertools.product((0, 1), repeat=r):
                    wild = list(zip(dims, signs))[::-1]
                    p = s
                    for dim, sg in wild:
                        p = step(p, dim, sg, D, twist)
                    tails = [dor_hops(p, d, D, twist)] if stage == "shortest" else dor_variants(p, d, D, twist)
                    for tail in tails:
                        if walk(s, wild + tail, D, twist, bad) == d:
                            out.append((wild + tail, r))
        if out:
            break
    if not out:
        prev = {s: None}
        queue = collections.deque([s])
        while queue:
            p = queue.popleft()
            if p == d:
                break
            for dim in range(3):
                for sg in (0, 1):
                    q = step(p, dim, sg, D, twist)
                    if q not in prev and (p, q) not in bad:
                        prev[q] = (p, dim, sg)
                        queue.append(q)
        hops, p = [], d
        while prev[p]:
            p, dim, sg = prev[p]
            hops.append((dim, sg))
        out = [(hops[::-1], -1)]
    out.sort(key=lambda c: len(c[0]))
    uniq, seen = [], set()
    for h, w in out:
        if tuple(h) not in seen and len(h) <= len(out[0][0]) + 2:
            seen.add(tuple(h))
            uniq.append((h, w))
    return uniq


def translations(D):
    sym = [min(CUBE, n) for n in D]
    return sym, list(itertools.product(*[range(0, D[k], sym[k]) for k in range(3)]))


def translate(p, shift, D, twist):
    """Walk the offset in +X, +Y, +Z: an automorphism of the plain and twisted
    torus that maps the cube-periodic fault set to itself."""
    for dim in range(3):
        for _ in range(shift[dim]):
            p = step(p, dim, 0, D, twist)
    return p


def optimize(D, twist, bad, iters=40):
    """Canonical routes {(source in the first cube, dest): hops}."""
    N = D[0] * D[1] * D[2]
    sym, shifts = translations(D)
    canon = [coord(i, D) for i in range(N) if all(coord(i, D)[k] < sym[k] for k in range(3))]
    lid = {}

    def links(s, hops):
        out = []
        for t in shifts:
            p = translate(s, t, D, twist)
            for dim, sg in hops:
                q = step(p, dim, sg, D, twist)
                out.append(lid.setdefault((p, q), len(lid)))
                p = q
        return out
    keys, pairs = [], []
    for s in canon:
        for di in range(N):
            d = coord(di, D)
            if d == s:
                continue
            cands = candidates(s, d, D, twist, bad)
            keys.append((s, d, cands))
            pairs.append([links(s, h) for h, _ in cands])
    choice = [0] * len(pairs)
    load = collections.Counter()
    for c in pairs:
        for l in c[0]:
            load[l] += 1
    for _ in range(iters):
        changed = 0
        for i, c in enumerate(pairs):
            if len(c) == 1:
                continue
            for l in c[choice[i]]:
                load[l] -= 1

            def cost(path):
                cnt = collections.Counter(path)
                return (max(load[l] + n for l, n in cnt.items()), sum(load[l] for l in path))
            j = min(range(len(c)), key=lambda k: cost(c[k]))
            if j != choice[i]:
                changed += 1
                choice[i] = j
            for l in c[j]:
                load[l] += 1
        if not changed:
            break
    return {(s, d): cands[choice[i]] for i, (s, d, cands) in enumerate(keys)}, max(load.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--dims", type=int, nargs=3, default=[8, 8, 16])
    ap.add_argument("--twisted", action="store_true")
    ap.add_argument("--ocs-fault", choices=list("xyz"))
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="adjacent")
    ap.add_argument("--vcs", type=int, choices=[2, 3], default=2,
                    help="VCs for fault routes: 2 (wild hops share them) or 3 (wild hops get VC 2)")
    ap.add_argument("--ocs-distance", type=float, default=OCS_DISTANCE_M,
                    help="TPU-to-OCS fiber length in metres (default %(default)s)")
    a = ap.parse_args()
    D = a.dims
    assert all(n % CUBE == 0 for n in D), "dimensions must be multiples of 4"
    twist = twist_type(D) if a.twisted else 0
    global fault_dim
    fault_dim = "xyz".index(a.ocs_fault) if a.ocs_fault else None
    N = D[0] * D[1] * D[2]
    bad = faults_for(a.scenario, D, fault_dim, twist) if a.ocs_fault else set()
    lat = {0: LAT_COPPER, 1: fiber_latency(a.ocs_distance)}

    # Links: ports 1-6 = X+, X-, Y+, Y-, Z+, Z- before failed links are removed.
    links, nbr = [], set()
    for i in range(N):
        p = coord(i, D)
        for dim in range(3):
            q = step(p, dim, 0, D, twist)
            if (p, q) in bad:
                continue
            kind = 1 if p[dim] % CUBE == CUBE - 1 else 0
            links.append((i, 1 + 2 * dim, index(q, D), 2 + 2 * dim, kind))
            nbr |= {(i, dim, 0), (index(q, D), dim, 1)}

    if bad:
        canon, busiest = optimize(D, twist, bad)
    else:
        canon, busiest = None, None
    sym, _ = translations(D)
    routes, chips_of, deps, fallback = [], [], {}, 0
    for s in range(N):
        ps = coord(s, D)
        if canon is not None:
            base = tuple(c % m for c, m in zip(ps, sym))
            shift = [c - b for c, b in zip(ps, base)]
        for dd in range(N):
            if canon is None:
                d, hops, nwild = dd, dor_hops(ps, coord(dd, D), D, twist), 0
            else:
                pd = coord(dd, D)
                hops, nwild = ([], 0) if pd == base else canon[(base, pd)]
                d = index(translate(pd, shift, D, twist), D)
            fallback += nwild < 0
            route, chips, p, prev, vc = [], [], ps, None, 0
            for i, (dim, sg) in enumerate(hops):
                chips.append(index(p, D))
                assert (index(p, D), dim, sg) in nbr, ("route uses a failed link", ps, dd)
                wrap = (p[dim] == D[dim] - 1 and sg == 0) or (p[dim] == 0 and sg == 1)
                if i < nwild:
                    v = 2 if a.vcs == 3 else 0 if wrap else 1
                else:
                    if dim != prev or i == nwild:
                        vc = 0
                    if wrap:
                        vc = 1
                    v = vc
                route.append((1 + 2 * dim + sg, v))
                deps.setdefault((chips[-1], dim, sg, v), set())
                if route[1:]:
                    deps[last].add((chips[-1], dim, sg, v))
                last = (chips[-1], dim, sg, v)
                p, prev = step(p, dim, sg, D, twist), dim
            assert index(p, D) == d
            routes.append((s, d, route))
            chips_of.append(chips)

    # Number each router's link ports 1..n without the gaps failed links leave.
    used = collections.defaultdict(set)
    for u, pu, v, pv, _ in links:
        used[u].add(pu)
        used[v].add(pv)
    remap = {u: {p: i + 1 for i, p in enumerate(sorted(ps))} for u, ps in used.items()}
    out_links = [(u, remap[u][pu], v, remap[v][pv], k, lat[k]) for u, pu, v, pv, k in links]
    routes = [(s, d, [(remap[c][p], vc) for c, (p, vc) in zip(chips, r)])
              for (s, d, r), chips in zip(routes, chips_of)]
    os.makedirs(a.outdir, exist_ok=True)
    fabricio.write_topology(os.path.join(a.outdir, "topo.txt"), N, out_links)
    fabricio.write_paths(os.path.join(a.outdir, "paths.txt"), routes)
    hops = [len(r) for _, _, r in routes]
    acyclic = cdgcheck.find_cycle(deps) is None
    print(f"{a.outdir}: {N} chips, {len(out_links)} links ({len(bad) // 2} failed), "
          f"mean {sum(hops) / len(hops):.3f} hops, max {max(hops)}"
          + (f", busiest link {busiest} under all-to-all" if busiest else "")
          + f", {1 + max(v for _, _, r in routes for _, v in r)} VCs"
          + (f", {fallback} fallback paths" if fallback else ""))
    print("channel dependencies: " + ("acyclic (deadlock-free)" if acyclic else
          "CYCLE: these routes can deadlock in a wormhole router (try --vcs 3)"))


if __name__ == "__main__":
    main()
