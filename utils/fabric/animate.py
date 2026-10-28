#!/usr/bin/env python3
"""Animated SVGs of collectives moving through a fabric, for the docs.

Each animation shows the bandwidth-bound steps of an all-reduce, the same
steps collectives.py turns into traffic matrices: a reduce-scatter followed
by the mirror all-gather. Dots are transfers, moving hop by hop over the
links the exporters build; the timing is one step (or hop) per beat, not to
scale. The SVGs use CSS animations only, so they play inline on GitHub
(`![](../media/x.svg)`).

  torus     4x4x4 torus (one cube), three concurrent bidirectional rings
            (shards on X,Y,Z / Y,Z,X / Z,X,Y, colored by shard)
  boardfly  Boardfly slice of --groups groups (default 4): tray, group and
            global stages over minimal paths

usage: animate.py torus OUT.svg
       animate.py boardfly OUT.svg [--groups G]
"""

import argparse
import heapq
import math

import boardfly
import torus

BG, LINK, CHIP, INK, MUTED = "#ffffff", "#d5d9e0", "#5b6472", "#1f2329", "#6b7280"
DARK = {"BG": "#0f1115", "LINK": "#2c313a", "CHIP": "#9aa3b2", "INK": "#e6e8eb", "MUTED": "#9aa3b2"}
SHARD = ["#d9480f", "#1971c2", "#2f9e44"]  # shards A, B, C
STAGE = {"tray": "#d9480f", "group": "#1971c2", "global": "#9c36b5"}


class Svg:
    def __init__(self, w, h, period):
        self.w, self.h, self.period = w, h, period
        self.css, self.body, self.n = [], [], 0

    def keyframes(self, frames):
        """frames: [(t_seconds, 'css decls')]; returns an animation class."""
        self.n += 1
        name = f"k{self.n}"
        steps = "".join(f"{100 * t / self.period:.3f}%{{{d}}}" for t, d in frames)
        self.css.append(f"@keyframes {name}{{{steps}}}.{name}{{animation:{name} {self.period}s linear infinite}}")
        return name

    def render(self, title):
        css = (f"svg{{background:{BG}}}.l{{stroke:{LINK};fill:none}}.c{{fill:{CHIP}}}"
               f"text{{font:13px system-ui,sans-serif;fill:{INK}}}.m{{fill:{MUTED};font-size:12px}}"
               ".d{offset-rotate:0deg}"
               f"@media (prefers-color-scheme:dark){{svg{{background:{DARK['BG']}}}"
               f".l{{stroke:{DARK['LINK']}}}.c{{fill:{DARK['CHIP']}}}text{{fill:{DARK['INK']}}}"
               f".m{{fill:{DARK['MUTED']}}}}}" + "".join(self.css))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img"><title>{title}</title>'
                f"<style>{css}</style>" + "".join(self.body) + "</svg>\n")


def window(t0, t1, period, on="opacity:1", off="opacity:0"):
    """Keyframes visible only in [t0, t1]."""
    e = 0.001 * period
    fr = [(0, off)] if t0 > e else []
    fr += [(max(0, t0 - e), off), (t0, on), (t1, on), (min(period, t1 + e), off)]
    if t1 + e < period:
        fr.append((period, off))
    return fr


def labels(svg, items, x, y):
    """items: [(t0, t1, text)] shown one at a time."""
    for t0, t1, text in items:
        k = svg.keyframes(window(t0, t1, svg.period))
        svg.body.append(f'<text x="{x}" y="{y}" class="{k}">{text}</text>')


def path_d(pts):
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


# --- torus -----------------------------------------------------------------

def torus_svg():
    D, sx, cx, cy, sz = (4, 4, 4), 78, 40, 30, 120
    ox, oy = 90, 560

    def pos(p):
        x, y, z = p
        return ox + x * sx + y * cx, oy - z * sz - y * cy

    def hop(p, d, sg):
        q = torus.step(p, d, sg, D, 0)
        a, b = pos(p), pos(q)
        if abs(q[d] - p[d]) == 1:
            return f"M{a[0]:.1f} {a[1]:.1f} L{b[0]:.1f} {b[1]:.1f}"
        # Wrap link: bow it away from the cube.
        bow = {0: (0, 34), 1: (-34, 0), 2: (-46, 0)}[d]
        mx, my = (a[0] + b[0]) / 2 + bow[0], (a[1] + b[1]) / 2 + bow[1]
        return f"M{a[0]:.1f} {a[1]:.1f} Q{mx:.1f} {my:.1f} {b[0]:.1f} {b[1]:.1f}"

    beat, steps = 0.7, 3  # a 4-chip ring reduce-scatters in 3 steps
    phases = []  # (t0, kind, phase) for 3 reduce-scatter + 3 all-gather phases
    t = 0.6
    for kind in ("rs", "ag"):
        for ph in range(3):
            phases.append((t, kind, ph))
            t += steps * beat + 0.35
        t += 0.6
    period = t + 0.4
    svg = Svg(660, 620, period)
    N = 64
    for i in range(N):
        p = torus.coord(i, D)
        for d in range(3):
            svg.body.append(f'<path class="l" d="{hop(p, d, 1)}"/>')
    for i in range(N):
        x, y = pos(torus.coord(i, D))
        svg.body.append(f'<circle class="c" cx="{x:.1f}" cy="{y:.1f}" r="5"/>')
    # Per dimension: color and size by phase; motion is the same every step.
    # Reduce-scatter phase k moves S/3 / (2 * 4^(k+1)) per direction per step.
    for d in range(3):
        fr_fill, fr_move = [], []
        for t0, kind, ph in phases:
            shard = (d - ph) % 3 if kind == "rs" else (d - (2 - ph)) % 3
            lvl = ph if kind == "rs" else 2 - ph
            scale = [1.0, 0.62, 0.4][lvl]
            for s in range(steps):
                a = t0 + s * beat
                b = a + beat * 0.85
                st = f"fill:{SHARD[shard]};transform:scale({scale})"
                fr_move += [(a, f"offset-distance:0%;opacity:0;{st}"),
                            (a + 0.05, f"offset-distance:4%;opacity:1;{st}"),
                            (b - 0.05, f"offset-distance:96%;opacity:1;{st}"),
                            (b, f"offset-distance:100%;opacity:0;{st}")]
        fr_move = [(0, "opacity:0")] + fr_move + [(period, "opacity:0")]
        k = svg.keyframes(fr_move)
        for i in range(N):
            p = torus.coord(i, D)
            for sg in (0, 1):
                svg.body.append(f'<circle class="d {k}" r="4.5" style="offset-path:path(\'{hop(p, d, sg)}\')"/>')
    svg.body.append('<text x="20" y="28" style="font-weight:600">Ring all-reduce on a 4x4x4 torus (64 chips)</text>')
    names = "ABC"
    items = []
    for t0, kind, ph in phases:
        t1 = t0 + steps * beat + 0.3
        order = [(names[(d - ph) % 3] if kind == "rs" else names[(d - (2 - ph)) % 3]) for d in range(3)]
        what = "reduce-scatter" if kind == "rs" else "all-gather"
        lvl = ph + 1 if kind == "rs" else 3 - ph
        items.append((t0, t1, f"{what}, phase {lvl} of 3: shard {order[0]} on X, {order[1]} on Y, "
                              f"{order[2]} on Z"))
    labels(svg, items, 20, 50)
    svg.body.append(f'<text x="20" y="604" class="m">Every link busy in both directions; '
                    f'18 neighbour steps; dots shrink as each shard is reduced. Not to scale.</text>')
    return svg.render("Ring all-reduce on a 4x4x4 torus")


# --- Boardfly --------------------------------------------------------------

def bf_svg(groups):
    sl = boardfly.build(groups)
    links = sl["links"] + [dict(u=g * 32 + r, v=h * 32 + r, kind="G") for g, r, h in sl["gports"]]
    adj = {}
    for l in links:
        adj.setdefault(l["u"], []).append((l["v"], l["kind"]))
        adj.setdefault(l["v"], []).append((l["u"], l["kind"]))

    def route(s, d):
        # Minimal: fewest global hops, then fewest hops (as boardfly.py).
        dist, prev, q = {s: (0, 0)}, {}, [((0, 0), s)]
        while q:
            c, u = heapq.heappop(q)
            if u == d:
                break
            if c > dist[u]:
                continue
            for v, k in sorted(adj[u]):
                nc = (c[0] + (k == "G"), c[1] + 1)
                if nc < dist.get(v, (9, 99)):
                    dist[v], prev[v] = nc, u
                    heapq.heappush(q, (nc, v))
        p = [d]
        while p[-1] != s:
            p.append(prev[p[-1]])
        return p[::-1]

    # Layout: groups on a 2-column grid, trays on a circle, chips 2x2 per tray.
    R, gap = 118, 330
    cols = 2 if groups > 1 else 1
    rows = math.ceil(groups / cols)
    W, H = 40 + cols * gap, 120 + rows * gap

    def pos(n):
        g, c = divmod(n, 32)
        t, k = divmod(c, 4)
        gx, gy = 20 + gap / 2 + (g % cols) * gap, 80 + gap / 2 + (g // cols) * gap
        a = 2 * math.pi * t / 8 - math.pi / 2
        tx, ty = gx + R * math.cos(a), gy + R * math.sin(a)
        return tx + (k % 2 - 0.5) * 16, ty + (k // 2 - 0.5) * 16

    hopt = 0.32
    jobs = []  # (stage, src, dst)
    for g in range(groups):
        for i in range(32):
            s, t, c = g * 32 + i, i // 4, i % 4
            jobs += [("tray", s, g * 32 + t * 4 + x) for x in range(4) if x != c]
            jobs += [("group", s, g * 32 + x * 4 + c) for x in range(8) if x != t]
            jobs += [("global", s, h * 32 + i) for h in range(groups) if h != g]
    paths = [(st, route(s, d)) for st, s, d in jobs]
    maxh = {st: max(len(p) - 1 for s2, p in paths if s2 == st) for st in STAGE}
    stages = [st for st in STAGE if st in maxh and (groups > 1 or st != "global")]
    win, t = {}, 0.6
    for kind in ("rs", "ag"):
        for st in (stages if kind == "rs" else stages[::-1]):
            win[(kind, st)] = (t, t + maxh[st] * hopt)
            t += maxh[st] * hopt + 0.7
        t += 0.6
    period = t + 0.4
    svg = Svg(W, H, period)
    for l in links:
        (x1, y1), (x2, y2) = pos(l["u"]), pos(l["v"])
        style = ' style="stroke:#b197fc;stroke-dasharray:3 3;opacity:.6"' if l["kind"] == "G" else ""
        svg.body.append(f'<line class="l" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"{style}/>')
    for n in range(groups * 32):
        x, y = pos(n)
        svg.body.append(f'<circle class="c" cx="{x:.1f}" cy="{y:.1f}" r="3.6"/>')
    # One keyframe class per (stage, hop count): reduce-scatter runs the path
    # forward, all-gather runs it backward.
    size = {"tray": 1.0, "group": 0.7, "global": 0.6}
    cls = {}
    for st, p in paths:
        h = len(p) - 1
        if (st, h) in cls:
            continue
        fr = [(0, "opacity:0")]
        for kind in ("rs", "ag"):
            if (kind, st) not in win:
                continue
            a = win[(kind, st)][0]
            b = a + h * hopt
            o0, o1 = ("0%", "100%") if kind == "rs" else ("100%", "0%")
            sc = f"transform:scale({size[st]})"
            fr += [(a, f"offset-distance:{o0};opacity:0;{sc}"), (a + 0.04, f"offset-distance:{o0};opacity:1;{sc}"),
                   (b - 0.04, f"offset-distance:{o1};opacity:1;{sc}"), (b, f"offset-distance:{o1};opacity:0;{sc}")]
        fr.append((period, "opacity:0"))
        cls[(st, h)] = svg.keyframes(sorted(fr, key=lambda f: f[0]))
    for st, p in paths:
        svg.body.append(f'<circle class="d {cls[(st, len(p) - 1)]}" r="4" fill="{STAGE[st]}" '
                        f'style="offset-path:path(\'{path_d([pos(n) for n in p])}\')"/>')
    n = groups * 32
    svg.body.append(f'<text x="20" y="28" style="font-weight:600">Hierarchical all-reduce on a '
                    f'{groups}-group Boardfly slice ({n} chips)</text>')
    desc = {"tray": "tray: 3 peers in the tray, S/4 each, copper",
            "group": "group: 7 peers at the same tray position, S/32 each, over copper",
            "global": f"global: {groups - 1} peers at the same chip index, S/{32 * groups} each, over fiber"}
    items = []
    for (kind, st), (a, b) in win.items():
        what = "reduce-scatter" if kind == "rs" else "all-gather"
        items.append((a, b + 0.5, f'{what}: <tspan fill="{STAGE[st]}">{desc[st]}</tspan>'))
    labels(svg, items, 20, 50)
    steps = 2 * len(stages)
    svg.body.append(f'<text x="20" y="{H - 16}" class="m">{steps} steps; dashed lines are fiber '
                    f'through the OCS. Dots move one hop per beat over minimal paths. Not to scale.</text>')
    return svg.render(f"Hierarchical all-reduce on a {groups}-group Boardfly slice")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("torus")
    t.add_argument("out")
    b = sub.add_parser("boardfly")
    b.add_argument("out")
    b.add_argument("--groups", type=int, default=4, choices=[1, 2, 4])
    a = ap.parse_args()
    svg = torus_svg() if a.cmd == "torus" else bf_svg(a.groups)
    with open(a.out, "w") as f:
        f.write(svg)
    print(f"{a.out}: {len(svg) // 1024} KB")


if __name__ == "__main__":
    main()
