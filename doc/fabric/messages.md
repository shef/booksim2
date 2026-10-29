# Message workloads (design)

Status: design, not implemented. Tracked in [ROADMAP.md](../ROADMAP.md),
section 1.

## Why

BookSim's workloads are open loop: terminals inject packets to random
destinations at a rate, and the result is a delivered rate. ML traffic is
collectives: each step sends fixed bytes between fixed pairs, waits for
earlier steps, and is judged by when the last byte lands. Open-loop runs
misread such traffic whenever per-pair load is unequal (the NSDI Table 2
study), and random destinations cannot express "every pair gets exactly the
same bytes" even in a paced batch (`batch_paced`).

A message workload runs a fixed list of messages with dependencies and
reports completion times. It must serve every system the fork models (tori,
group networks, switched fabrics; chips with different link counts, flit
sizes and clocks), so it knows nothing about collectives or about any one
system: generators in `utils/fabric/` turn a collective on a system into
messages.

## Model

- **Endpoint**: a router that has terminals (a chip). Endpoints are numbered
  by router index, so a fabric with switches, or chips with different
  terminal counts, needs no renumbering. On upstream topologies (no
  `topology = fabric`) every node is an endpoint.
- **Message**: `bytes` from one endpoint to another. It is cut into packets
  of `packet_size` flits of `flit_bytes` bytes each (the last packet may be
  shorter).
- **Ready time**: a message with no dependencies is ready at time 0;
  otherwise when the last of its dependencies has finished, plus its
  `delay` (compute time before sending, such as a reduction).
- **Finish time**: when the tail flit of its last packet is ejected at the
  destination.
- **Injection**: a message's packets are spread over its source endpoint's
  terminals round robin, and each goes to the destination terminal with the
  same index modulo the destination's terminal count, so one chip drives all
  its injection ports, as a DMA engine would. A terminal with several ready
  messages interleaves their packets round robin, one packet at a time
  (fair sharing, no message starves). Packets go out as fast as the
  terminal and network accept them; there is no rate parameter, because the
  question is how long the work takes.
- **Pipelining** is the generator's choice: a step that forwards data as it
  arrives is several smaller messages (chunks), each depending on the
  matching incoming chunk.
- **End of run**: when every message has finished. If messages remain and no
  flit has been ejected for `message_stall_cycles`, the run stops and lists
  the unfinished messages and the channels their packets wait in: a
  deadlock, or a dependency that can never be met.

Units: sizes are bytes and delays nanoseconds, so one message file serves
any system; the configuration converts them with `flit_bytes` and
`cycle_ns`, which belong to the system's calibration (for example 200 B and
1 ns for the TPU 8 model).

## Message file (`message_file`)

One message per line; `#` starts a comment.

```
# id  src  dst  bytes   [after=ID,ID,...] [delay=NS] [tag=NAME]
0     0    1    262144  tag=rs0
1     1    2    262144  tag=rs0
2     1    2    262144  after=0 delay=50 tag=rs1
```

- `id`: integers in increasing order. A dependency must name an earlier id,
  so the dependency graph cannot have a cycle.
- `src`, `dst`: endpoint (router) indices; must have terminals and differ.
- `tag`: groups messages for reporting, typically a collective step or
  phase. Tags are free text without spaces.

A global barrier is a message list's dependencies; collectives rarely need
more than a handful per message (a ring step depends on one incoming
message, a hierarchical stage on the few it reduces). A 1,024-chip
all-to-all is about a million lines with no dependencies.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `sim_type = messages` | | run the message file instead of open-loop traffic |
| `message_file` | | the message list |
| `flit_bytes` | 200 | bytes per flit, from the system calibration |
| `cycle_ns` | 1.0 | nanoseconds per cycle, from the system calibration |
| `message_stall_cycles` | 1000000 | cycles without an ejected flit before a run with unfinished messages stops |
| `message_out_file` | | optional per-message results: id, tag, ready, first injection, finish (cycles) |

## Output

- Total: messages, bytes, completion time (cycles and ns), and effective
  throughput (bytes per ns per endpoint).
- Per tag: message count, bytes, earliest ready, latest finish, and the
  tag's span in ns.
- Optional per-message file for detailed analysis and for the viewer.
- Stalled runs exit with status 2 after listing what is stuck.

## Implementation

A `MessageTrafficManager` subclass, chosen by `sim_type = messages`, like
the batch manager:

- `TrafficManager` gets two virtual hooks, `_PacketDestination(source, cl)`
  and `_PacketSize(source, cl)`, defaulting to today's traffic pattern and
  packet size; `_GeneratePacket` calls them instead of computing both
  inline. Upstream behaviour is unchanged (golden tests).
- `_IssuePacket` picks the next packet for a terminal from its ready
  messages (round robin) and remembers its destination, size and message;
  the packet id is mapped to its message.
- `_RetireFlit` counts tail flits per message; the last one finishes the
  message, and dependents whose dependencies are all done become ready at
  the current time plus their delay.
- `_SingleSim` steps until all messages finish or the stall limit is hit.
- The fabric network exposes each router's terminal range, used to map
  endpoints to nodes.

Memory is per message (counters and dependency lists), so a million
messages are fine. Simulation time is proportional to flits moved:
generators keep sizes modest and long collectives are simulated for a few
steps, with per-step tags to check the steady state, then extrapolated.

## Generators

`utils/fabric/collectives.py` gains message output alongside its traffic
matrices, for any topology file:

- all-to-all with equal bytes per pair;
- ring all-reduce over given rings (the torus's three bidirectional rings,
  Boardfly's Hamiltonian rings), reduce-scatter then all-gather, optionally
  chunked;
- hierarchical all-reduce over given groups (tray, group, global);
- reduction compute as `delay`, from a per-system bytes-per-ns figure.

## Tests

1. One message on a known path: finish = path latency + serialization.
2. Two messages sharing one link take twice as long as one.
3. A dependency chain adds up; `delay` is applied.
4. A chip with 2 terminals finishes a message in half the time of one with 1
   (if the network is not the limit); heterogeneous terminal counts work.
5. A deadlocking route set stops at the stall limit and names the stuck
   messages.
6. Equal-bytes all-to-all on 4x4x4, fault-free against one OCS down: the
   ratio of completion times matches the busiest-link bound (94.1%) within
   about 2%. This is the measurement the NSDI study lacked.
7. Ring all-reduce time follows the 2(N-1) steps x (S/N)/B formula.

## Out of scope for now, noted for later

- Different bandwidths on different links (BookSim moves one flit per
  cycle on every channel; mixed-rate systems would need per-link rates).
- Priorities or traffic classes per message.
- Chakra execution-trace import (a converter to this format).
- Making `traffic = matrix` use the same router-based endpoints (it assumes
  equal terminal counts today).
