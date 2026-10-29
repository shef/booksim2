"""Writers for the fabric file formats (doc/fabric/formats.md)."""


def write_topology(path, routers, links, terminals=None, compute=None):
    """links: (u, port_u, v, port_v, kind, latency[, rate]) with link ports
    from 1; terminals: optional {router: count or (count, rate)} overriding
    fabric_terminals; compute: optional {router: count or (count, rate)} of
    compute terminals. Rates are flits per cycle, written only when below 1."""
    with open(path, "w") as f:
        f.write(f"{routers}\n")
        for tag, table in (("t", terminals), ("c", compute)):
            for r, n in sorted((table or {}).items()):
                n, rate = n if isinstance(n, tuple) else (n, 1)
                f.write(f"{tag} {r} {n}" + (f" {rate:g}" if rate < 1 else "") + "\n")
        for l in links:
            u, pu, v, pv, kind, lat = l[:6]
            rate = l[6] if len(l) > 6 else 1
            f.write(f"{u} {pu} {v} {pv} {kind} {lat}" + (f" {rate:g}" if rate < 1 else "") + "\n")


def write_tables(path, rows, arrdown):
    """rows: R x R entries (min, ud0, ud1, mask[, layer]) in row-major order;
    arrdown: per router, the bitmask of link ports whose arrivals are down hops."""
    with open(path, "w") as f:
        f.write("".join(" ".join(map(str, r)) + "\n" for r in rows))
        f.write("".join(f"{x}\n" for x in arrdown))


def write_paths(path, routes):
    """routes: (src router, dest router, [(link port, vc), ...])."""
    with open(path, "w") as f:
        for s, d, hops in routes:
            f.write(f"{s} {d} {len(hops)} " + " ".join(f"{p} {v}" for p, v in hops) + "\n")


def write_matrix(path, endpoints, flows):
    """flows: {(src endpoint, dst endpoint): weight}."""
    with open(path, "w") as f:
        f.write(f"{endpoints}\n")
        for (s, d), w in sorted(flows.items()):
            f.write(f"{s} {d} {w:.9g}\n")


def write_messages(path, msgs):
    """msgs: (src, dst, bytes, deps, delay_ns, tag[, stream]) with ids 0, 1,
    ... in list order; deps and stream name earlier ids; src and dst are
    router indices or "cR" for compute terminals (doc/fabric/messages.md)."""
    with open(path, "w") as f:
        f.write("# id src dst bytes [after=...] [stream=...] [delay=NS] [tag=...]\n")
        for i, m in enumerate(msgs):
            s, d, b, deps, delay, tag = m[:6]
            stream = m[6] if len(m) > 6 else ()
            line = f"{i} {s} {d} {b}"
            if deps:
                line += " after=" + ",".join(map(str, deps))
            if stream:
                line += " stream=" + ",".join(map(str, stream))
            if delay:
                line += f" delay={delay}"
            if tag:
                line += f" tag={tag}"
            f.write(line + "\n")
