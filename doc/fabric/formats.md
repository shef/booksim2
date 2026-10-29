# Fabric file formats

`topology = fabric` builds a network from files instead of a built-in shape.
It is meant for accelerator scale-up fabrics: tori, group networks such as
dragonfly variants, and switched fabrics. Topology, routing tables and source
routes are written by external tools, so a new system needs an exporter, not
new C++.

## Routers, terminals and nodes

Every router is a chip or a switch. Router `r` has `fabric_terminals`
terminals (default 1) unless the topology file overrides it; a switch has 0.
A router's terminals take its ports `0..T-1`, and nodes are numbered
contiguously in router order: router 0's terminals first, then router 1's, and
so on. Several terminals let one chip inject and eject several links' worth
of traffic, as an accelerator's DMA engines do.

## Topology file (`network_file`)

The router count, then one record per line; `#` starts a comment.

```
4                     # routers
t 3 0                 # router 3 has no terminals (a switch)
t 2 4 0.25            # router 2 has 4 terminals, each limited to 0.25 flits/cycle
0 1 3 1 0 10          # U PORT_U V PORT_V KIND LATENCY: a bidirectional link
0 2 2 1 2 1080 0.125  # ... with an optional RATE in flits per cycle each way
```

Rates (optional, `0 < RATE <= 1`, default 1) model links slower than the
fabric's flit-per-cycle base rate, such as PCIe or NIC links beside ICI,
older generations, or links running at reduced width; a terminal rate caps
how fast that router's terminals inject and eject (DMA engines, host
links). A link with a rate sends a flit when it has earned a whole one at
that rate (a token bucket), and keeps a small output buffer so it backs up
through credits like any congested link. Rates need `router = iq`.

Link ports are numbered from 1 on each router, without gaps (port 0 in the
files means "this router's terminals"). On the router they sit after the
terminal ports. Parallel links between two routers are allowed. Up to 63 link
ports per router.

`KIND` is a small integer whose meaning depends on the routing function:

| Routing | KIND |
|---|---|
| `dor` | dimension + 4 if the link is that ring's dateline (wraparound) |
| `esc`, `updown`, `duato`, `ugal` | 0 and 1 local links, 2 global links; groups are the connected components of the non-global links |
| `src`, `lduato` | ignored, except that `lduato` and `ugal` derive groups as above |

`LATENCY` is the channel latency in cycles, used for both flits and credits.

## Routing tables (`fabric_table_file`)

`R x R` entries in row-major order (current router, destination router), one
per line:

```
MIN UD0 UD1 MASK [LAYER]
```

| Field | Meaning |
|---|---|
| `MIN` | a minimal next-hop link port (`-1` on the diagonal) |
| `UD0`, `UD1` | up*/down* escape port when the packet arrived on an up hop (or was injected) / on a down hop |
| `MASK` | bit `p` set if link port `p` is a minimal next hop (adaptive routing) |
| `LAYER` | escape layer, present when `fabric_esc_layers > 0` |

Then one line per router: a bitmask of the link ports whose arrivals are down
hops in the up*/down* tree.

## Source routes (`fabric_path_file`)

One line per (source router, destination router):

```
SRC DEST N  PORT VC  PORT VC ...
```

with the link port and VC of each of the `N` hops.

## Traffic matrix (`traffic_matrix_file`)

The endpoint count `E`, then `SRC DST WEIGHT` lines. Endpoints are consecutive
blocks of `nodes / E` nodes (a chip's terminals); a node's destination
endpoint is drawn with its row's weights and the node within it uniformly.

```
4
0 1 1
1 2 1
2 3 1
3 0 1
```
