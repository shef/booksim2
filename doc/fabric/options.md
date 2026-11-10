# Fabric options

## Traffic and measurement

| Option | Effect |
|---|---|
| `traffic = matrix` | endpoint-to-endpoint weights from `traffic_matrix_file`: the endpoint count `E`, then `SRC DST WEIGHT` lines; endpoints are consecutive blocks of `nodes / E` nodes (a chip's terminals) |
| `traffic = groupshift(G)` | every node of group `g` sends to a random node of group `g+1`, groups being blocks of `G` nodes |
| `measure_reorder = 1` | reports packets that arrived behind a later packet of the same source-destination pair, and the resequencing delay an in-order receiver would add (only for runs that converge) |
| `batch_paced = 1` | with `sim_type = batch`, packets are issued at `injection_rate` (upstream batch mode ignores it and injects as fast as it can, so every batch runs overloaded); the batch time is then a completion time below saturation. Destinations still follow `traffic`, so a uniform batch is not equal bytes per pair |
| `sim_type = messages` | runs `message_file`, a list of messages with dependencies, and reports completion times; keys `flit_bytes` (200), `cycle_ns` (1.0), `message_stall_cycles` (1000000), `message_window` (0: packets in flight per endpoint, unlimited), `message_window_classes` and `message_window_latency` (a window per path-latency class), `message_out_file`; use with `speculative = 1` ([messages.md](messages.md)) |
| `stats_json = FILE` | writes the run's results as JSON: `run` (status ok, unstable or stalled; cycles; the git commit the binary was built from), `config` (every key), `traffic_class_N` (overall latency, rates, hops) and `messages` (completion, throughput, per-tag spans). Scripts should read this instead of parsing the text output |
| `link_util_file = FILE` | fabric only: one CSV row per link direction (router, port, to_router, kind, rate, flits, utilization of that link's rate over the whole run, warm-up and drain included for open-loop runs); also adds busiest and mean utilization to `stats_json` |

## Network keys

| Option | Default | Effect |
|---|---|---|
| `network_file` | | topology file (`formats.md`) |
| `fabric_terminals` | 1 | terminals per router unless the topology file overrides it |
| `fabric_table_file` | | routing tables |
| `fabric_path_file` | | source routes for `src` |
| `fabric_esc_layers` | 0 | escape layers in the tables (`lduato`, `ugal`) |
| `ugal_threshold` | 0 | UGAL bias toward the minimal path, in used credits x hops |
| `fabric_eject_any` | 0 | 1: a packet may leave its destination router through any terminal of the same kind (chip or compute), least occupied first, one packet per port at a time; without it each packet's terminal is chosen at the source (in message runs rotated per packet, offset by message id). Helps where incast varies (all-to-all, trees); costs up to about 12% on ring and point-to-point traffic (doc/fabric/messages.md, Clos example) |

At start-up the fabric network warns when a VC's buffer (`vc_buf_size`, or
`buf_size` with a shared `buffer_policy`) is below some link's credit round
trip, about 2 x latency x rate flits: one VC then cannot keep that link busy,
however traffic is paced. Calibrated fiber links (1,080 cycles) need 2,160
flits, more than the default 512, which is why the TPU configs share a
5,242-flit buffer per port.

## Example and tests

`src/examples/fabric/fabric_config` runs a 4x4x4 torus with dimension-order
source routes. `make -C src test` runs `tests/run_tests.py`.
