# Collectives viewer

An interactive page that shows collectives moving through the modelled
fabrics in 3D, and what virtual channels do inside a link. It is for
explaining the systems, not for measuring them: the paths are the exporters'
real wiring and routes, but time is schematic.

![All-reduce on a 4x4x4 torus](../media/torus-allreduce.svg)

## Opening it

The page loads its scenes with `fetch`, so serve the folder rather than
opening the file directly:

```
cd doc/viewer && python3 -m http.server
# open http://localhost:8000, or http://localhost:8000/#ring-dateline for one scene
```

| Control | Action |
|---|---|
| Drag | rotate (3D scenes) |
| Wheel, pinch | zoom |
| Click a chip | follow the transfers through it; click again to stop |
| Timeline | drag to any moment; labelled segments mark the phases |
| Space, Play | pause and resume |
| ‹ Step, Step ›, arrow keys | jump between phases |
| `?t=12` | open paused at beat 12 (for screenshots) |

On narrow screens the explanation panel moves below the scene.

## Scenes

| Scene | What it shows | Source of the paths |
|---|---|---|
| `torus-allreduce` | 4x4x4 torus (one cube): three concurrent bidirectional rings, reduce-scatter then all-gather | `torus.py` coordinates, dimension-order rings |
| `torus-alltoall` | the same torus, every chip sending to every other | `torus.py` dimension-order routes |
| `boardfly-allreduce` | 4-group Boardfly: tray, group and global stages | `boardfly.py` slice wiring |
| `boardfly-alltoall` | the same slice, every chip to every other | `boardfly.py` minimal paths (fewest global hops, then fewest hops) |
| `ring-deadlock` | 4-chip ring, one virtual channel: a burst fills the buffers in a cycle and nothing can move | `flitsim.py` |
| `ring-bigbuffer` | the same burst with buffers twice as deep: more traffic gets through, then the same deadlock | `flitsim.py` |
| `ring-dateline` | two virtual channels with a dateline: the cycle is broken and every packet arrives | `flitsim.py` |

The fabric scenes are grouped as "Fabric" in the page, the ring scenes as
"Inside the links". Each scene has a beginner's explanation of the
algorithm (what all-reduce and all-to-all are, why rings, what a VC is).

**What is to scale and what is not.**

- Fabric scenes: one hop takes one beat, whatever the link (copper or
  fiber). Link colours mark the link kind: board trace, copper cable or
  fiber through the OCS. The step counts are real (for example 6 steps for
  Boardfly's hierarchical all-reduce against 18 for the 4x4x4 torus rings);
  durations are not. Simulated times and throughputs are on the system pages.
- Ring scenes: a cycle-by-cycle run of a small flit simulator
  (`utils/fabric/flitsim.py`) with wormhole switching, credit flow control
  and round-robin arbitration, so every flit can be drawn. It follows the
  same rules as BookSim's router (a head flit enters a VC once the previous
  tail has arrived), but it is not BookSim output. Deadlock here is
  Dally and Seitz's (1987) cyclic channel dependency; the dateline is how
  the torus routes in this fork stay deadlock-free
  ([routing.md](../fabric/routing.md), [tpu-torus.md](../systems/tpu-torus.md)).

## Rebuilding

```
python3 utils/fabric/scenes.py doc/viewer/data            # all scene files
python3 utils/fabric/animate.py torus doc/media/torus-allreduce.svg
python3 utils/fabric/animate.py boardfly doc/media/boardfly-allreduce.svg
```

Both are deterministic: rebuilding gives identical files. The scene file
format is described at the top of `scenes.py`; the SVGs use CSS animation
only, so they play inline on GitHub where pages cannot run scripts.

## Planned

Real BookSim runs replayed in the viewer (per-link utilization, a watched
packet's path, failure cases with fault routes), 1,024-chip slices, a
real-time mode with modelled latencies, side-by-side comparison, and
Trainium and NVLink scenes.
