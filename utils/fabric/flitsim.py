#!/usr/bin/env python3
"""A tiny flit-level simulator of a unidirectional ring, for the viewer's
virtual-channel scenes (scenes.py ring-*).

Model, deliberately small so every flit can be drawn:
  - N routers in a ring; router r sends to r+1 over one link. Each router has
    one input port (from r-1) with `vcs` virtual channels of `cap` flits.
  - Packets of `plen` flits, injected at their source from an unbounded queue
    straight onto the outgoing link, ejected at their destination.
  - Wormhole switching: a head flit may enter a downstream VC once the
    previous packet's tail has arrived there, so packets queue one behind
    another but their flits never interleave in a VC (BookSim with
    wait_for_tail_credit = 0). Credits return instantly.
  - Each cycle a link carries one flit, chosen round-robin among the VCs at
    the head of the line and the injection queue; each VC ejects one flit.
  - VC rule: everything uses VC 0, or with `dateline` a packet moves to VC 1
    when it crosses the link from N-1 to 0, and stays there.

run() returns one frame per cycle (the state at the start of the cycle) and
stops when the ring drains or after it has been stalled for a few cycles.
"""

import collections
import random


def schedule(n, rate, cycles, seed):
    """Bernoulli injection: per node per cycle, a packet with probability
    `rate` to a destination 1 to n-1 hops away."""
    rng = random.Random(seed)
    out = []
    for c in range(cycles):
        for r in range(n):
            if rng.random() < rate:
                out.append((c, r, (r + rng.randint(1, n - 1)) % n))
    return out


def run(sched, n=4, vcs=1, cap=4, plen=4, dateline=False, max_cycles=300, stall_frames=6):
    packets = []  # [src, dst]
    inj = [collections.deque() for _ in range(n)]  # [pid, next flit]
    buf = [[collections.deque() for _ in range(vcs)] for _ in range(n)]  # (pid, flit)
    owner = [[None] * vcs for _ in range(n)]  # packet still arriving in each VC
    last = [-1] * n  # round robin: the source served last has the lowest priority
    pending = sorted(sched)
    frames, delivered, stalled = [], 0, 0

    def next_vc(r, v):
        return 1 if dateline and r == n - 1 else v

    for cyc in range(max_cycles):
        while pending and pending[0][0] == cyc:
            _, s, d = pending.pop(0)
            packets.append([s, d])
            inj[s].append([len(packets) - 1, 0])
        # Moves, all decided from the state at the start of the cycle.
        ejects, moves, waits = [], [], []
        for r in range(n):
            for v in range(vcs):
                if buf[r][v] and packets[buf[r][v][0][0]][1] == r:
                    ejects.append((r, v))
            t = (r + 1) % n
            cands = []  # (source index, source, packet, flit, downstream VC); VCs first, then injection
            for v in range(vcs):
                if buf[r][v] and packets[buf[r][v][0][0]][1] != r:
                    pid, fi = buf[r][v][0]
                    cands.append((v, ("b", v), pid, fi, next_vc(r, v)))
            if inj[r]:
                pid, fi = inj[r][0]
                cands.append((vcs, ("i",), pid, fi, next_vc(r, 0)))
            ok = []
            for k, src, pid, fi, tv in cands:
                room = len(buf[t][tv]) < cap
                free = owner[t][tv] == pid if fi else owner[t][tv] is None
                if room and free:
                    ok.append((k, src, pid, fi, tv))
                elif src[0] == "b" and fi == 0:
                    waits.append([r, src[1], t, tv])
            if ok:
                pick = min(ok, key=lambda c: (c[0] - last[r] - 1) % (vcs + 1))
                last[r] = pick[0]
                moves.append((r, t) + pick[1:])
        frames.append(dict(
            b=[[list(f) for f in buf[r][v]] for r in range(n) for v in range(vcs)],
            q=[[list(p) for p in inj[r]] for r in range(n)],
            w=waits, d=delivered))
        for r, v in ejects:
            pid, fi = buf[r][v].popleft()
            if fi == plen - 1:
                delivered += 1
        for r, t, src, pid, fi, tv in moves:
            if src[0] == "b":
                buf[r][src[1]].popleft()
            else:
                inj[r][0][1] += 1
                if inj[r][0][1] == plen:
                    inj[r].popleft()
            owner[t][tv] = None if fi == plen - 1 else pid
            buf[t][tv].append((pid, fi))
        busy = any(buf[r][v] for r in range(n) for v in range(vcs)) or any(inj)
        stalled = stalled + 1 if busy and not ejects and not moves else 0
        if stalled >= stall_frames:
            break
        if not busy and not pending:
            frames.append(dict(b=[[] for _ in range(n * vcs)], q=[[] for _ in range(n)], w=[], d=delivered))
            break
    deadlock = stalled >= stall_frames
    return dict(packets=packets, frames=frames, deadlock=deadlock, delivered=delivered,
                n=n, vcs=vcs, cap=cap, plen=plen, dateline=dateline)
