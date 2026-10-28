# Fabric routing functions

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

## Deadlock freedom

- `dor`: Dally and Seitz's dateline rule; each ring's channels form a cycle
  that the VC 0 to VC 1 switch at the dateline breaks.
- `updown`, `esc`: up*/down* or VC escalation keep the channel dependency
  graph acyclic, given tables from a spanning tree.
- `duato`, `lduato`, `ugal`: adaptive VCs may form cycles; the escape VCs
  never do, and a packet can always take an escape VC (Duato's condition).
  `lduato` gives each group hop its own escape layer, so the escape network
  stays acyclic for any group path length, including detours around failed
  links. The exporter is responsible for the tables meeting these rules.
- `src`: the path file's VCs must be deadlock-free; the network does not
  check them. `utils/fabric/cdgcheck.py TOPO PATHS` builds the channel
  dependency graph (channel = router, output port, VC) and reports a cycle if
  there is one; with wormhole switching, no cycle means no deadlock (Dally and
  Seitz, 1987). A cycle means a deadlock is possible, not that a run will hit
  it.
