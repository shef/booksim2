#!/usr/bin/env python3
"""Scenes for the collectives viewer (doc/viewer/): JSON files holding a
topology laid out in 3D, the transfers of a collective as timed paths over
its links, and a labelled timeline.

Paths come from the exporters' wiring: torus.py's coordinates and
dimension-order routes, boardfly.py's slice and minimal paths (fewest global
hops, then fewest hops). Time is in beats: one hop takes one beat, which is
not to scale.

  torus-allreduce     4x4x4 torus, three concurrent bidirectional rings
  torus-alltoall      4x4x4 torus, dimension-order routes
  boardfly-allreduce  4-group Boardfly, tray -> group -> global stages
  boardfly-alltoall   4-group Boardfly, minimal paths
  ring-deadlock       4-chip ring, one virtual channel: a burst deadlocks
  ring-bigbuffer      the same with buffers twice as deep: deadlocks later
  ring-dateline       two virtual channels with a dateline: all delivered

Scene format: {title, intro, kinds: {k: {name, color, dash}}, nodes:
[[x, y, z, label]], links: [[u, v, kind, ctrl or null]], palette: [color],
events: [[t, color, scale, [node, ...]]], segments: [[t0, t1, title, text,
color]], period, follow}. `ctrl` is the control point of a curved link.
Ring scenes (`type: "ring"`) carry flitsim.run()'s output instead of nodes,
links and events: {type, n, vcs, cap, plen, dateline, packets: [[src, dst]],
frames: [{b: per (router, VC) the buffered [packet, flit], q: per router the
queued [packet, next flit], w: waits [router, VC, wanted router, wanted VC],
d: delivered}], deadlockAt}, plus title, intro, segments and period; time is
in cycles.

usage: scenes.py OUTDIR
"""

import argparse
import heapq
import json
import math
import os

import boardfly
import flitsim
import torus

YELLOW, PINK, GREEN, CYAN = "#ffd43b", "#ff6b9a", "#69db7c", "#66d9e8"

ALLREDUCE = ("<b>All-reduce</b> gives every chip the sum of every chip's data. Training uses it "
             "after each step: every chip computes gradients on its own batch, and all of them "
             "need the total. Large all-reduces run in two halves. In the <b>reduce-scatter</b> "
             "half, chips trade pieces and add what they receive, until each chip owns the "
             "finished sum for one slice of the buffer. In the <b>all-gather</b> half, each chip "
             "sends its finished slice to everyone, so every chip ends with the whole sum. Each "
             "dot is one transfer.")
ALLTOALL = ("<b>All-to-all</b> sends a different piece of data from every chip to every other "
            "chip, and nothing is added up. Mixture-of-experts models use it to send each token "
            "to the chip holding its expert, and back. Every piece travels its own path, so the "
            "cost is set by how many hops paths take and how evenly they spread over the links. "
            "Each dot is one piece; click a chip to follow only its traffic.")


def r3(v):
    return [round(x, 3) for x in v]


def scene(title, intro, kinds, nodes, links, palette, events, segments, follow=None):
    period = max(s[1] for s in segments) + 1.0
    return dict(title=title, intro=intro, kinds=kinds, nodes=nodes, links=links,
                palette=palette, events=events, segments=segments, period=period, follow=follow)


# --- torus -----------------------------------------------------------------

D = (4, 4, 4)
TORUS_KINDS = {"c": dict(name="copper inside the cube", color="#c8875a", dash=False),
               "f": dict(name="fiber through the OCS (wraparound)", color="#7aa2f7", dash=True)}


def torus_base():
    c = [(n - 1) / 2 for n in D]
    nodes = []
    for i in range(64):
        x, y, z = torus.coord(i, D)
        nodes.append(r3([x - c[0], z - c[2], y - c[1]]) + [f"chip {i} (x{x} y{y} z{z})"])
    links = []
    for i in range(64):
        p = torus.coord(i, D)
        for d in range(3):
            q = torus.step(p, d, 0, D, 0)
            j = torus.index(q, D)
            if q[d] == 0:  # wraparound: bow it outside the cube
                a, b = nodes[i][:3], nodes[j][:3]
                mid = [(a[k] + b[k]) / 2 for k in range(3)]
                axis = {0: 0, 1: 2, 2: 1}[d]  # node coords are (x, z, y)
                off = [mid[k] if k != axis else 0 for k in range(3)]
                n = math.sqrt(sum(o * o for o in off)) or 1
                ctrl = r3([mid[k] + off[k] / n * 2.6 for k in range(3)])
                links.append([i, j, "f", ctrl])
            else:
                links.append([i, j, "c", None])
    return nodes, links


def torus_allreduce():
    nodes, links = torus_base()
    names = "ABC"
    events, segments, t = [], [], 0.5
    for kind in ("rs", "ag"):
        for ph in range(3):
            lvl = ph if kind == "rs" else 2 - ph
            shard = [(d - lvl) % 3 for d in range(3)]
            scale = [1.0, 0.7, 0.5][lvl]
            for s in range(3):
                for i in range(64):
                    p = torus.coord(i, D)
                    for d in range(3):
                        for sg in (0, 1):
                            j = torus.index(torus.step(p, d, sg, D, 0), D)
                            events.append([round(t + s * 1.15, 2), shard[d], scale, [i, j]])
            what = "Reduce-scatter" if kind == "rs" else "All-gather"
            order = ", ".join(f"{names[shard[d]]} on {'XYZ'[d]}" for d in range(3))
            if kind == "rs":
                text = (f"Each ring of 4 chips passes pieces around in 3 steps, adding as it goes. "
                        f"Shards move along: {order}. After this phase each shard is summed over "
                        f"{4 ** (ph + 1)} chips and each chip keeps a quarter of what it held, so "
                        f"the dots shrink.")
            else:
                text = (f"The reverse: finished slices are copied back around the rings ({order}), "
                        f"and each chip's share grows fourfold.")
            segments.append([round(t, 2), round(t + 3 * 1.15, 2),
                             f"{what} {lvl + 1 if kind == 'rs' else 3 - ph}/3", text, None])
            t += 3 * 1.15 + 0.6
        t += 0.8
    intro = (ALLREDUCE + " <br><br>On a torus the data is split into three shards, A, B and C, one per "
             "colour. Each shard reduces along X, then Y, then Z, starting in a different "
             "dimension, so all six links of every chip are busy at every step. That uses all "
             "the bandwidth, but it takes 18 steps one after another, and each step waits for "
             "the slowest link.")
    return scene("Ring all-reduce, 4x4x4 torus (64 chips)", intro, TORUS_KINDS, nodes, links,
                 [YELLOW, PINK, GREEN], events, segments)


def torus_alltoall():
    nodes, links = torus_base()
    events, hops = [], []
    for s in range(64):
        for d in range(64):
            if s == d:
                continue
            p, path = torus.coord(s, D), [s]
            for dim, sg in torus.dor_hops(p, torus.coord(d, D), D, 0):
                p = torus.step(p, dim, sg, D, 0)
                path.append(torus.index(p, D))
            events.append([0.5, 0, 1.0, path])
            hops.append(len(path) - 1)
    mx = max(hops)
    text = (f"Every chip sends to all 63 others at once over dimension-order routes (X, then Y, "
            f"then Z). Paths average {sum(hops) / len(hops):.1f} hops, at most {mx}. "
            f"The links between neighbours each carry many pieces.")
    segs = [[0.5, 0.5 + mx, "All-to-all", text, None]]
    intro = ALLTOALL + (" <br><br>On a torus a piece moves one dimension at a time, so the hop count "
                        "grows with the slice: 3 on average here, about 8 on an 8x8x16 slice.")
    return scene("All-to-all, 4x4x4 torus (64 chips)", intro, TORUS_KINDS, nodes, links,
                 [YELLOW], events, segs, follow=0)


# --- Boardfly --------------------------------------------------------------

GROUPS = 4
BF_KINDS = {"T": dict(name="in-tray links (4 chips per tray)", color="#9aa3b2", dash=False),
            "C": dict(name="copper between trays", color="#c8875a", dash=False),
            "G": dict(name="fiber through the OCS, between groups", color="#7aa2f7", dash=True)}


def bf_base():
    sl = boardfly.build(GROUPS)
    raw = sl["links"] + [dict(u=g * 32 + r, v=h * 32 + r, kind="G") for g, r, h in sl["gports"]]
    R, w, gap = 2.2, 0.32, 2.6
    nodes = []
    for n in range(GROUPS * 32):
        g, c = divmod(n, 32)
        t, k = divmod(c, 4)
        a = 2 * math.pi * t / 8
        tan, rad = (k % 2 - 0.5) * w, (k // 2 - 0.5) * w
        x = (R + rad) * math.cos(a) - tan * math.sin(a)
        z = (R + rad) * math.sin(a) + tan * math.cos(a)
        y = (g - (GROUPS - 1) / 2) * gap
        nodes.append(r3([x, y, z]) + [f"chip {n} (group {g}, tray {t}, position {k})"])
    links = []
    for l in raw:
        u, v, kind = l["u"], l["v"], l["kind"]
        ctrl = None
        if kind == "G":
            a, b = nodes[u][:3], nodes[v][:3]
            span = abs(u // 32 - v // 32)
            rr = math.hypot(a[0], a[2])
            push = 1 + 0.22 * span
            ctrl = r3([a[0] * push, (a[1] + b[1]) / 2, a[2] * push])
        links.append([u, v, kind, ctrl])
    adj = {}
    for u, v, kind, _ in links:
        adj.setdefault(u, []).append((v, kind))
        adj.setdefault(v, []).append((u, kind))
    return nodes, links, adj


def bf_route(adj, s, d):
    """Minimal path: fewest global hops, then fewest hops (as boardfly.py)."""
    dist, prev, q = {s: (0, 0)}, {}, [((0, 0), s)]
    while q:
        c, u = heapq.heappop(q)
        if u == d:
            break
        if c > dist[u]:
            continue
        for v, k in sorted(adj[u]):
            nc = (c[0] + (k == "G"), c[1] + 1)
            if nc < dist.get(v, (99, 99)):
                dist[v], prev[v] = nc, u
                heapq.heappush(q, (nc, v))
    p = [d]
    while p[-1] != s:
        p.append(prev[p[-1]])
    return p[::-1]


def bf_allreduce():
    nodes, links, adj = bf_base()
    stages = ["tray", "group", "global"]
    jobs = {st: [] for st in stages}
    for g in range(GROUPS):
        for i in range(32):
            s, t, c = g * 32 + i, i // 4, i % 4
            jobs["tray"] += [bf_route(adj, s, g * 32 + t * 4 + x) for x in range(4) if x != c]
            jobs["group"] += [bf_route(adj, s, g * 32 + x * 4 + c) for x in range(8) if x != t]
            jobs["global"] += [bf_route(adj, s, h * 32 + i) for h in range(GROUPS) if h != g]
    color = {"tray": 0, "group": 1, "global": 2}
    scale = {"tray": 1.0, "group": 0.75, "global": 0.6}
    n = GROUPS * 32
    rs_text = {
        "tray": "Each chip splits its buffer in 4 and sends a quarter to each of the 3 other chips "
                "in its tray, over the short in-tray links. Each chip adds what it receives: it now "
                "owns the tray's sum for one quarter.",
        "group": "Each chip sends an eighth of its quarter to the chip in the same position in each "
                 "of the other 7 trays. Trays are joined by copper cables, two per chip, so most "
                 "pieces take a detour through a neighbour in the tray: up to 3 hops. Now each chip "
                 "owns the group's sum for 1/32 of the buffer.",
        "global": f"Each chip trades its 1/32 with the chip of the same index in the other "
                  f"{GROUPS - 1} groups, over fiber through the OCS. Some take one extra hop inside "
                  f"each group, to the chip that holds the fiber to that group and back. Each chip now owns the full sum of "
                  f"1/{n} of the buffer.",
    }
    ag_text = {
        "global": "All-gather starts: finished slices go back to the same chip index in every group.",
        "group": "Slices spread to the same position in every tray of the group.",
        "tray": "Finally every chip shares with its tray, and every chip holds the whole sum.",
    }
    events, segments, t = [], [], 0.5
    for kind in ("rs", "ag"):
        for st in (stages if kind == "rs" else stages[::-1]):
            mh = max(len(p) - 1 for p in jobs[st])
            for p in jobs[st]:
                events.append([round(t, 2), color[st], scale[st], p if kind == "rs" else p[::-1]])
            title = ("Reduce-scatter: " if kind == "rs" else "All-gather: ") + st
            segments.append([round(t, 2), round(t + mh, 2), title,
                             rs_text[st] if kind == "rs" else ag_text[st], [YELLOW, PINK, GREEN][color[st]]])
            t += mh + 0.8
        t += 0.8
    intro = (ALLREDUCE + " <br><br>Boardfly reduces level by level: first inside each tray of 4 chips, then "
             "across the 8 trays of a group, then across groups. Only the last stage uses fiber, "
             "and it carries the least data. With two or more groups that is 6 steps, and adding "
             "groups adds none. Torus rings need one step per chip along each ring: 18 here, 58 "
             "at 1,024 chips. A Boardfly step can take several hops, one of them over fiber, but "
             "a small all-reduce still finishes much sooner: at 1,024 chips, with 100 m to the "
             "OCS and counting link latency only, about 3.5 µs against about 63 µs for the "
             "torus rings, where every step waits for a fiber hop.")
    return scene(f"Hierarchical all-reduce, {GROUPS}-group Boardfly ({n} chips)", intro, BF_KINDS,
                 nodes, links, [YELLOW, PINK, GREEN], events, segments)


def bf_alltoall():
    nodes, links, adj = bf_base()
    n = GROUPS * 32
    events, hops = [], []
    for s in range(n):
        for d in range(n):
            if s != d:
                p = bf_route(adj, s, d)
                events.append([0.5, 0, 1.0, p])
                hops.append(len(p) - 1)
    mx = max(hops)
    text = (f"Every chip sends to all {n - 1} others at once over minimal paths. Paths average "
            f"{sum(hops) / len(hops):.1f} hops, at most {mx}, and cross at most one fiber link.")
    intro = ALLTOALL + (" <br><br>Boardfly keeps paths short as the slice grows: at most 7 hops at "
                        "1,024 chips (6.5 on average), against 16 at most (8 on average) on an "
                        "8x8x16 torus. At these small sizes the two are close: 3.0 hops on "
                        "average on the 64-chip torus, 3.2 on this 128-chip slice.")
    return scene(f"All-to-all, {GROUPS}-group Boardfly ({n} chips)", intro, BF_KINDS, nodes, links,
                 [YELLOW], events, [[0.5, 0.5 + mx, "All-to-all", text, None]], follow=0)


# --- virtual channels on a ring -------------------------------------------

# Six packets that get through, then a burst: within a few cycles each chip
# sends three packets of 2 or 3 hops.
RING_SCHEDULE = [(0, 0, 1), (1, 2, 0), (3, 1, 3), (5, 3, 1), (8, 0, 2), (9, 2, 3), (14, 0, 3),
                 (14, 1, 0), (15, 0, 3), (16, 0, 3), (16, 1, 3), (16, 2, 1), (16, 2, 1), (16,
                 3, 1), (18, 1, 0), (19, 3, 2), (20, 2, 0), (20, 3, 2)]
RING_COLORS = [YELLOW, PINK, GREEN, CYAN]
VC_BASICS = ("A link carries data as <b>flits</b>, small fixed-size pieces of a packet (4 flits "
             "per packet here). The receiving chip keeps a buffer per link. A packet's first flit, "
             "its <b>head</b>, can enter a buffer once the previous packet's last flit has "
             "arrived there, so packets queue one behind another and their flits never mix. "
             "Here 4 chips form a ring and send clockwise, one flit per link per cycle; each link "
             "takes turns between traffic passing through and the chip's own packets. ")


def ring_scene(name):
    kw = {"ring-deadlock": dict(vcs=1, cap=4), "ring-bigbuffer": dict(vcs=1, cap=8),
          "ring-dateline": dict(vcs=2, cap=4, dateline=True)}[name]
    stall = 12
    r = flitsim.run(RING_SCHEDULE, stall_frames=stall, **kw)
    frames = r["frames"]
    end = len(frames) - 1
    burst = min(c for c, _, _ in RING_SCHEDULE if c >= 12)
    segs = [[0, burst, "Traffic flows",
             "Packets of 4 flits travel clockwise and are delivered. A head sometimes waits a "
             "cycle for a buffer to empty, then moves on.", None]]
    if r["deadlock"]:
        dl = end - stall + 1
        segs += [[burst, dl, "All four chips send at once",
                  "Every chip sends three packets of 2 or 3 hops. Buffers fill up as heads wait "
                  "for room in the next buffer." +
                  (" The deeper buffers soak up more of the burst, and more packets get through."
                   if kw["cap"] > 4 else ""), None],
                 [dl, end, "Deadlock",
                  "Every buffer is full, and the packet at the front of each one waits for room in "
                  "the next. The red arrows close a loop, so no flit can ever move again." +
                  (" The deeper buffers only postponed it: once all of them were full, the same "
                   "loop formed." if kw["cap"] > 4 else ""), "#ff6b6b"]]
    else:
        dl = None
        segs += [[burst, end, "All four chips send at once",
                  "The same burst. Packets that cross the dateline, from chip 3 to chip 0, move "
                  "to VC 1. Heads still wait for room, but the waits can never close a loop.", None],
                 [end, end + 3, "All delivered",
                  f"All {r['delivered']} packets arrived. Two lanes, used in a fixed order, "
                  "cannot form a loop of waits.", GREEN]]
    intro = {
        "ring-deadlock": VC_BASICS + "<br><br>With a single lane per link, packets can end up "
            "in a circle, each holding one buffer while it waits for the next. None can move, "
            "forever: a <b>deadlock</b>. Red arrows show which packet waits for which buffer.",
        "ring-bigbuffer": VC_BASICS + "<br><br>Would deeper buffers help? Here every buffer "
            "holds 8 flits instead of 4, and the same burst is sent. The buffers absorb more of "
            "it and more packets arrive, but once every buffer is full the same loop of waits "
            "closes. Storage only postpones a deadlock. Preventing it takes routing whose "
            "dependencies cannot form a loop (Dally and Seitz, 1987), which is what virtual "
            "channels provide.",
        "ring-dateline": VC_BASICS + "<br><br>The fix is a second <b>virtual channel</b> (VC): "
            "each link gets two lanes with separate buffers, sharing the wire. Packets start on "
            "VC 0 and move to VC 1 when they cross the <b>dateline</b>, the link from chip 3 "
            "back to chip 0. VC 0 is never used on that link, and a packet on VC 1 never "
            "reaches it again, so neither lane can close a loop. This is why the torus model "
            "uses 2 VCs, one dateline per ring. Boardfly's escape VCs use the same idea, "
            "moving up one layer per group hop.",
    }[name]
    title = {"ring-deadlock": "One lane: deadlock", "ring-bigbuffer": "Deeper buffers: deadlock later",
             "ring-dateline": "Two lanes and a dateline: no deadlock"}[name]
    return dict(type="ring", title=f"{title} (4-chip ring)", intro=intro, n=r["n"], vcs=r["vcs"],
                cap=r["cap"], plen=r["plen"], dateline=r["dateline"], packets=r["packets"],
                frames=frames, deadlockAt=dl, palette=RING_COLORS, segments=segs,
                period=segs[-1][1] + 0.5)


SCENES = {"torus-allreduce": torus_allreduce, "torus-alltoall": torus_alltoall,
          "boardfly-allreduce": bf_allreduce, "boardfly-alltoall": bf_alltoall,
          "ring-deadlock": lambda: ring_scene("ring-deadlock"),
          "ring-bigbuffer": lambda: ring_scene("ring-bigbuffer"),
          "ring-dateline": lambda: ring_scene("ring-dateline")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    for name, fn in SCENES.items():
        path = os.path.join(a.outdir, name + ".json")
        with open(path, "w") as f:
            json.dump(fn(), f, separators=(",", ":"))
        print(f"{path}: {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    main()
