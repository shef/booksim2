BookSim Interconnection Network Simulator
=========================================

BookSim is a cycle-accurate interconnection network simulator.
Originally developed for and introduced with the [Principles and Practices of Interconnection Networks](http://cva.stanford.edu/books/ppin/) book, its functionality has since been continuously extended.
The current major release, BookSim 2.0, supports a wide range of topologies such as mesh, torus and flattened butterfly networks, provides diverse routing algorithms and includes numerous options for customizing the network's router microarchitecture.

## What this fork adds

This fork extends BookSim to model accelerator scale-up fabrics (TPU ICI
tori and group networks, with Trainium NeuronLink and NVLink planned) so they
can be compared under the same traffic. Upstream behaviour is unchanged: the
upstream example configs reproduce their recorded statistics exactly.

- **`topology = fabric`**: a network read from files: routers that are chips
  (with any number of terminals) or switches (none), links with per-link
  latency, and routing from offline tables or source routes. Routing
  functions: `src`, `dor`, `esc`, `updown`, `duato`, `lduato` (Duato with a
  layered escape network, deadlock-free under link failures) and `ugal`.
- **`traffic = matrix`**: an endpoint-to-endpoint traffic matrix from a file,
  so collective phases (all-reduce stages, all-to-all) run as steady traffic.
  Also `groupshift(G)`, an adversarial pattern for group networks.
- **`measure_reorder = 1`**: per-pair packet reordering and the resequencing
  delay an in-order receiver would add.
- **Tests**: `make -C src test` (upstream golden outputs plus fabric checks
  against analytical values), run by GitHub Actions on every push.

Documentation: [doc/fabric.md](doc/fabric.md) (file formats, routing
functions, options). Example: [src/examples/fabric](src/examples/fabric).

```
make -C src
cd src/examples/fabric && ../../booksim fabric_config
make -C src test
```

---

If you use BookSim in your research, we would appreciate the following citation in any publications to which it has contributed:

Nan Jiang, Daniel U. Becker, George Michelogiannakis, James Balfour, Brian Towles, John Kim and William J. Dally. A Detailed and Flexible Cycle-Accurate Network-on-Chip Simulator. In *Proceedings of the 2013 IEEE International Symposium on Performance Analysis of Systems and Software*, 2013.