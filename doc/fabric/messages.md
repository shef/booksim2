# Message workloads

Status: implemented: the engine (`sim_type = messages`) and generators in
`utils/fabric/collectives.py` (`msg-*`); tests `fabric_messages_*` and
`utils_collective_messages`. Tracked in
[ROADMAP.md](../ROADMAP.md), section 1.

Use `speculative = 1` for message runs. BookSim's input-queued router by
default allocates a VC and then the switch, one after the other, so
back-to-back packets on one VC (every packet of a message follows the same
source route) lose a cycle per packet: one message ran at 8/9 of link rate
with 8-flit packets, and at half with 1-flit packets. Speculative switch
allocation, as high-performance routers do, removes the gap. Open-loop
traffic mixes VCs and inputs and mostly hides it: on the NSDI study's
saturation cases speculative allocation changed throughput by at most
0.7%.

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
  terminals round robin, and over the destination's terminals in rotation,
  so one chip drives all its injection ports, as a DMA engine would, and
  several chips can feed one multi-terminal endpoint at full rate. A terminal with several ready
  messages interleaves their packets round robin, one packet at a time
  (fair sharing, no message starves). Packets go out as fast as the
  terminal and network accept them; there is no rate parameter, because the
  question is how long the work takes.
- **Window** (`message_window`, packets): at most this many packets per
  source endpoint are in flight (issued, tail not yet ejected), as a
  transport's window or a DMA engine's outstanding-request limit bounds
  them. Without one, a large all-to-all is open-loop traffic far past
  saturation, and BookSim's input-queued routers then lose most of their
  throughput to head-of-line blocking (post-saturation collapse), worse the
  longer the run: on a 4x4x8 twisted torus, 48 KB per pair took 41,666
  cycles with links 40% busy, against 20,972 with a window of 512. Set it
  to cover the bandwidth-delay product (rate per endpoint x round trip):
  there, 384 to 1,024 packets gave the same time, and collapse returned
  past about 1,500. 0 (the default) means no limit.
- **Pipelining** is the generator's choice: a step that forwards data as it
  arrives is several smaller messages (chunks), each depending on the
  matching incoming chunk.
- **End of run**: when every message has finished. If messages remain and no
  flit has been ejected for `message_stall_cycles`, the run stops and lists
  the unfinished messages and the channels their packets wait in: a
  deadlock, or a dependency that can never be met.

Units: sizes are bytes and delays integer nanoseconds, so one message file
serves any system; the configuration converts them with `flit_bytes` and
`cycle_ns`, which belong to the system's calibration (for example 200 B and
1 ns for the TPU 8 model, 2 ns for Ironwood). This follows the common split
in other tools: workloads are described in bytes and absolute time (Chakra
traces, ASTRA-sim's network configuration in ns and GB/s, ns-3 and htsim,
NCCL measurements), while flits and cycles stay inside cycle-level
simulators (BookSim, Garnet). Results are reported in bytes, ns and GB/s per
endpoint so they compare directly with hardware measurements.

## Message file (`message_file`)

One message per line; `#` starts a comment.

```
# id  src  dst  bytes   [after=ID,...] [stream=ID,...] [delay=NS] [tag=NAME]
0     0    1    262144  tag=rs0
1     1    2    262144  tag=rs0
2     1    2    262144  after=0 delay=50 tag=rs1
```

- `id`: integers in increasing order. A dependency must name an earlier id,
  so the dependency graph cannot have a cycle.
- `src`, `dst`: endpoint (router) indices, or `cR` for router R's compute
  terminals; must have terminals and differ.
- `tag`: groups messages for reporting, typically a collective step or
  phase. Tags are free text without spaces.
- `stream`: earlier messages this one forwards as they arrive. Its k-th
  share of packets may go once the same share of each stream source has
  been delivered (shares scale by packet counts, so sizes may differ), plus
  `delay` per packet. Models cut-through reduction and relaying (in-switch
  or on-chip engines, multicast by relay); `after=` waits for whole
  messages instead.

A global barrier is a message list's dependencies; collectives rarely need
more than a handful per message (a ring step depends on one incoming
message, a hierarchical stage on the few it reduces).

### Size and format

A line is 30-40 bytes:

| Workload | Messages | Text | gzipped |
|---|---|---|---|
| all-to-all, 64 chips | 4,032 | 0.1 MB | small |
| all-to-all, 1,024 chips | about 1.05 million | 30-35 MB | 5-8 MB |
| ring all-reduce, 1,024-chip torus (3 bidirectional rings) | 150-200 thousand | 6-8 MB | about 1 MB |
| the same in 8 chunks | about 1.5 million | about 60 MB | about 10 MB |
| all-to-all, 9,216 chips | about 85 million | about 3 GB | too large |

Reading tens of MB takes a second or two, far less than simulating it, and
full pods are out of reach of packet-level simulation anyway (they go to a
flow-level model), so size does not pick the format. Plain text does: it can
be read, grepped and diffed, a test case can be written by hand, a stuck
message from a stalled run can be looked up with `grep`, and generators are
a few lines of Python. Compressed input (`.gz`) can be added if files grow.

Alternatives considered:

- JSON: two to three times larger for flat records, and BookSim has no
  parser. It suits the results instead (small, structured, read by Python
  and the viewer).
- CBOR or another binary encoding: about half the raw size, but gzipped
  text is smaller still, parsing speed does not matter here, and it costs
  readability and a C++ dependency. If a binary format is ever needed (tens
  of millions of messages), protobuf would be the one, to share Chakra's.
- Chakra execution traces (MLCommons): a level above this file. A Chakra
  trace is a per-rank graph of compute, collective and send/receive
  operations; a simulator's system layer decides how each collective becomes
  network messages, which is what this file holds. So Chakra is supported by
  a Python converter into this format (send/receive nodes map one to one;
  collective nodes go through the same generators), which keeps protobuf
  and collective algorithms out of BookSim.

The engine reads the file once into an in-memory list, so another reader
can be added later without touching the rest.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `sim_type = messages` | | run the message file instead of open-loop traffic |
| `message_file` | | the message list |
| `flit_bytes` | 200 | bytes per flit, from the system calibration |
| `cycle_ns` | 1.0 | nanoseconds per cycle, from the system calibration |
| `message_stall_cycles` | 1000000 | cycles without an ejected flit before a run with unfinished messages stops |
| `message_window` | 0 | packets in flight per source endpoint (0: no limit); set for bandwidth-bound runs, see Window above |
| `message_out_file` | | optional per-message results: id, tag, ready, first injection, finish (cycles) |

## Output

- Total: messages, bytes, completion time (cycles and ns), and effective
  throughput (bytes per ns per endpoint).
- Per tag: message count, bytes, earliest ready, latest finish, and the
  tag's span in ns.
- Optional per-message file for detailed analysis and for the viewer.
- Stalled runs exit with status 2 after listing what is stuck.

## Implementation

Exit status: 0 when every message finished, 2 when a run stalled (the fork
also fixes upstream BookSim's inverted status, which returned -1 on success
and 0 on failure; it now returns 0 and 1).

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

`utils/fabric/collectives.py` writes message files. A collective is a list
of stages; each stage splits the endpoints into groups that exchange either
around a ring (n - 1 steps to a neighbour) or directly (one step to every
peer). An all-reduce runs the stages as a reduce-scatter, each leaving 1/n
of the data, then in reverse as an all-gather; each send waits for the data
it forwards. A new system only needs its group lists.

| Command | Collective |
|---|---|
| `msg-alltoall` | every endpoint sends S/N to every other (`--endpoints N`, or `--topo` for a fabric with switches) |
| `msg-torus` | ring stages along X, Y, Z in three rotated orders and both directions, S/6 each |
| `msg-boardfly` | direct stages within trays, groups, then across groups |
| `msg-rings` | bidirectional ring over up to K edge-disjoint Hamiltonian cycles of a topology |
| `msg-ring` | bidirectional logical ring over the endpoints in order, routed by the fabric (for switched fabrics) |
| `msg-innet` | in-network all-reduce: each chip streams 1/K of its data to each of K compute-terminal engines (`--engines`), which stream the reduced result back (`stream=`) |
| `msg-bcast` | broadcast from one endpoint: `unicast` (every copy from the root; an upper bound, since BookSim has no multicast), `relay` (through compute-terminal engines, as switch multicast) or `tree` (endpoints relay down a binary tree as data arrives) |
| `msg-moe` | MoE expert-parallel all-to-all: each chip sends S to experts spread over the chips, weighted by Zipf popularity (`--skew`, 0 = uniform), local tokens stay local; `--combine` adds the return trip |
| `msg-pipeline` | pipeline parallel (GPipe): `--stages` groups of chips, `--microbatches` of activations forward then gradients back, `--compute-ns` per hop |
| `msg-tree` | tree all-reduce: the whole buffer reduced up a binary tree and broadcast down, chunk by chunk; `--double` uses two complementary trees with half each (NCCL's double binary tree); about 2 log2 N steps instead of 2(N-1), for latency-bound sizes and switched fabrics |

`--bytes S` is the buffer per chip, as in NCCL. `--chunks C` makes C
independent pipelines; `--reduce-gbps R` delays each forward of reduced
data by bytes / R; `--repeat L` runs L collectives back to back, each chip
starting the next after receiving all of the previous (inference decode:
one small all-reduce per layer). Steps are tagged (`rs0.3` is reduce-scatter stage 0,
step 3) so per-step spans show when a run reaches its steady state.

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

### Example: an 8-chip, 4-switch Clos

`clos.py --chips 8 --switches 4 --compute 8`, 1.6 MB per chip, links of one
flit per cycle, `speculative = 1`, `num_vcs = 4`, `vc_buf_size = 512`
(completion in cycles; "fixed" rotates each packet's destination terminal
at the source, "any" is `fabric_eject_any = 1`):

| Collective | One path, fixed | One path, any | `--multipath`, fixed | `--multipath`, any | Note |
|---|---|---|---|---|---|
| one message, chip to chip | 8,108 | 8,130 | 2,108 | 2,358 | 8,000 flits over one link, or four |
| in-network all-reduce (`msg-innet`) | 2,244 | 2,378 | 2,244 | 2,378 | each chip sends S once over its 4 links: ideal about 2,000 |
| all-to-all, 1.4 MB sent per chip | 2,724 | 2,388 | 3,040 | 2,860 | the fixed (i + j) mod K assignment is already balanced; spreading adds downlink collisions |
| ring all-reduce (`msg-ring`, 4 chunks) | 7,256 | 9,359 | 6,274 | 5,789 | one path per pair puts ring neighbours on 2 of the 4 switches |
| double binary tree (16 chunks) | 24,819 | 19,117 | 12,418 | 11,074 | inner chips carry the whole buffer |

Neither ejection mode wins everywhere: choosing the least occupied port
helps where incast varies (all-to-all, trees) and costs up to 12%
elsewhere. Before 2026-10-29 the fixed rotation was `(source terminal +
packet) mod terminals`, which kept one parity and used half the ports
(all-to-all 7,084 cycles); it is now `(message id + packet) mod terminals`.

Small all-reduces (16 KB, latency-bound, as in decode): in-network 151
cycles against 779 for a double binary tree; `--repeat 4` takes 607 and
3,142, four times one.

MoE dispatch, 1.6 MB per chip, 4 experts per chip: uniform popularity
2,388 cycles (the plain all-to-all), Zipf skew 1 5,387, skew 2 11,553; the
chips holding popular experts become incast hot spots.

Broadcast of 1.6 MB from one chip to the other seven (`--multipath`):
every copy from the root 14,109 cycles; relayed by the switches' compute
terminals 2,372 (the root sends the data once); relayed down a binary tree
of chips 6,172. Modelling multicast as unicast would overstate its cost
about six times here.

## In-network and near-network compute (design)

Some systems reduce or replicate data outside the main compute units:

| Where | Examples (public) | Effect |
|---|---|---|
| On the chip, beside the network | TPU 8i's Collectives Acceleration Engine in its ICI chiplet, which reduces at the package edge (about 5x lower on-chip latency, Hot Chips 2026); TPU 8t SparseCores serving collectives | faster reductions that do not use the main compute units or HBM, possibly as data streams in |
| In the switch | NVLink SHARP, InfiniBand SHARP | the switch reduces many inputs into one output and replicates results |
| Replication only | multicast for broadcast and all-gather | one stream in, many copies out |

Plan, in increasing engine change:

1. **Reduction profiles** (generators only): per-system reduction latency and
   throughput, applied as `delay`; an on-chip engine is a faster profile.
2. **Streaming dependencies** (implemented): `stream=ID` lets a message send
   each packet once the matching packet of message ID has arrived, instead
   of waiting for all of it. In-stream reducers work this way; chunking only
   approximates it, at the cost of more messages.
3. **Compute terminals** (implemented; test `fabric_compute_terminals`): a
   `c R N` line gives router R (a switch or a chip) N terminals that act as
   its reduction and replication engine; messages address them as `cR`. On
   four chips around one switch, an in-switch all-reduce (chips stream into
   the engine, the engine streams the result back) took 905 cycles against
   1,373 for a ring, as expected from S against 1.5 S per chip link. They are endpoints that messages can address, and
   reports count them apart from chips so per-chip throughput stays honest.
   An in-switch all-reduce is then a tree whose inner nodes are switch
   compute terminals; multicast is a compute terminal relaying one incoming
   message as several outgoing ones.

What a given engine does internally (whether it streams, whether it serves
neighbours or only its own chip) is often not published; such choices
become parameters with sensitivity runs, as with VC counts.

## Out of scope for now, noted for later

- Different bandwidths on different links (BookSim moves one flit per
  cycle on every channel; mixed-rate systems would need per-link rates).
- Priorities or traffic classes per message.
- Chakra execution-trace import (a converter to this format).
- Making `traffic = matrix` use the same router-based endpoints (it assumes
  equal terminal counts today).
