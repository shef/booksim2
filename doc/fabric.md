# Fabric network

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
0 1 3 1 0 10          # U PORT_U V PORT_V KIND LATENCY: a bidirectional link
```

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

## Routing functions

| `routing_function` | Needs | Behaviour |
|---|---|---|
| `src` | paths | fixed routes with explicit VCs, e.g. routes computed offline for a failed link |
| `dor` | tables (`MIN`) | torus dimension-order routing; VC 0 per dimension, VC 1 after the dateline |
| `esc` | tables | deterministic minimal; VC +1 after a global hop and on each kind 1 -> 0 turn |
| `updown` | tables | up*/down*; VC +1 after each global hop |
| `duato` | tables | adaptive on VCs `0..n-3`; escape on up*/down*, VC `n-2` outside the destination group, `n-1` inside |
| `lduato` | tables with layers | adaptive on VCs `0..n-L-1`; layered escape on VC `n-L+LAYER` (`L = fabric_esc_layers`) |
| `ugal` | tables with layers | `lduato` plus a source choice between minimal and a Valiant path through a random third group (`ugal_threshold` biases toward minimal) |

At exit the network reports how many packets finished on escape VCs and, for
`ugal`, the minimal/Valiant split.

## Related options

| Option | Effect |
|---|---|
| `traffic = matrix` | endpoint-to-endpoint weights from `traffic_matrix_file`: the endpoint count `E`, then `SRC DST WEIGHT` lines; endpoints are consecutive blocks of `nodes / E` nodes (a chip's terminals) |
| `traffic = groupshift(G)` | every node of group `g` sends to a random node of group `g+1`, groups being blocks of `G` nodes |
| `measure_reorder = 1` | reports packets that arrived behind a later packet of the same source-destination pair, and the resequencing delay an in-order receiver would add (only for runs that converge) |

## Example

`src/examples/fabric/fabric_config`: a 4x4x4 torus with dimension-order
source routes.

## Tests

`make -C src test` runs `tests/run_tests.py`: upstream example configs must
reproduce `tests/golden` exactly, and the fabric network is checked on tiny
topologies against analytical values (latency per hop, mean hop counts,
per-terminal injection through a switch) plus saturated runs of every routing
function.
