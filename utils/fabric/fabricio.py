"""Writers for the fabric file formats (doc/fabric/formats.md)."""


def write_topology(path, routers, links, terminals=None):
    """links: (u, port_u, v, port_v, kind, latency) with link ports from 1;
    terminals: optional {router: count} overriding fabric_terminals."""
    with open(path, "w") as f:
        f.write(f"{routers}\n")
        for r, n in sorted((terminals or {}).items()):
            f.write(f"t {r} {n}\n")
        for u, pu, v, pv, kind, lat in links:
            f.write(f"{u} {pu} {v} {pv} {kind} {lat}\n")


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
    """msgs: (src, dst, bytes, deps, delay_ns, tag) with ids 0, 1, ... in list
    order; deps name earlier ids (doc/fabric/messages.md)."""
    with open(path, "w") as f:
        f.write("# id src dst bytes [after=...] [delay=NS] [tag=...]\n")
        for i, (s, d, b, deps, delay, tag) in enumerate(msgs):
            line = f"{i} {s} {d} {b}"
            if deps:
                line += " after=" + ",".join(map(str, deps))
            if delay:
                line += f" delay={delay}"
            if tag:
                line += f" tag={tag}"
            f.write(line + "\n")
