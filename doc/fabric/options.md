# Fabric options

## Traffic and measurement

| Option | Effect |
|---|---|
| `traffic = matrix` | endpoint-to-endpoint weights from `traffic_matrix_file`: the endpoint count `E`, then `SRC DST WEIGHT` lines; endpoints are consecutive blocks of `nodes / E` nodes (a chip's terminals) |
| `traffic = groupshift(G)` | every node of group `g` sends to a random node of group `g+1`, groups being blocks of `G` nodes |
| `measure_reorder = 1` | reports packets that arrived behind a later packet of the same source-destination pair, and the resequencing delay an in-order receiver would add (only for runs that converge) |

## Network keys

| Option | Default | Effect |
|---|---|---|
| `network_file` | | topology file (`formats.md`) |
| `fabric_terminals` | 1 | terminals per router unless the topology file overrides it |
| `fabric_table_file` | | routing tables |
| `fabric_path_file` | | source routes for `src` |
| `fabric_esc_layers` | 0 | escape layers in the tables (`lduato`, `ugal`) |
| `ugal_threshold` | 0 | UGAL bias toward the minimal path, in used credits x hops |

## Example and tests

`src/examples/fabric/fabric_config` runs a 4x4x4 torus with dimension-order
source routes. `make -C src test` runs `tests/run_tests.py`.
