# Fabric exporters

Python 3 scripts (standard library only) that write the files `topology =
fabric` reads (doc/fabric/formats.md). Calibration constants sit at the top
of each exporter and are documented on the system pages (doc/systems/).
`pypy3` runs them several times faster on 1,024-chip slices.

| Script | Writes | System page |
|---|---|---|
| `boardfly.py` | TPU 8i slices with optional failures: topology, `lduato` tables, meta.json | [tpu-boardfly.md](../../doc/systems/tpu-boardfly.md) |
| `torus.py` | TPU 3D torus slices, plain or twisted, optionally with one OCS failure: topology and `src` routes | [tpu-torus.md](../../doc/systems/tpu-torus.md) |
| `collectives.py` | traffic matrices for all-reduce phases and ring all-reduce | |
| `cdgcheck.py` | checks a path file for channel dependency cycles (deadlock freedom of `src` routes) | [routing.md](../../doc/fabric/routing.md) |
| `scenes.py` | scene files for the collectives viewer ([doc/viewer/](../../doc/viewer/)) | |
| `flitsim.py` | the small flit-level ring simulator behind the viewer's virtual-channel scenes | |
| `animate.py` | animated SVGs of collectives for the docs | |
| `fabricio.py` | the file writers the exporters share | |

## Examples

```
# TPU 8i, 32 groups, OCS plane A down and the surviving plane re-planned;
# meta.json gives the escape VCs needed (fabric_esc_layers)
pypy3 boardfly.py /tmp/bf --groups 32 --ocs A --repair --verify
#   ... topology=fabric network_file=/tmp/bf/topo.txt fabric_table_file=/tmp/bf/tables.txt
#       routing_function=lduato fabric_esc_layers=3 num_vcs=5

# Ironwood-style twisted 8x8x16, one OCS failure in Z holding two face positions;
# wild hops on their own VC so the routes are provably deadlock-free
pypy3 torus.py /tmp/t --twisted --ocs-fault z --scenario adjacent --vcs 3
#   ... routing_function=src fabric_path_file=/tmp/t/paths.txt num_vcs=3
python3 cdgcheck.py /tmp/t/topo.txt /tmp/t/paths.txt

# All-reduce on the torus, six terminals per chip
python3 collectives.py torus-rings /tmp/ar.txt --twisted
#   ... traffic=matrix traffic_matrix_file=/tmp/ar.txt fabric_terminals=6

# Viewer scenes and the animated SVGs on the system pages (doc/viewer/README.md)
python3 scenes.py ../../doc/viewer/data
python3 animate.py torus ../../doc/media/torus-allreduce.svg
python3 animate.py boardfly ../../doc/media/boardfly-allreduce.svg
```

`--ocs-distance` sets the TPU-to-OCS fiber length (default 100 m, a 1,080 ns
fiber hop).
