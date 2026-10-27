# Documentation

This fork models accelerator scale-up fabrics in BookSim so that different
systems can be compared under the same traffic. The documentation has three
parts that change at different rates.

| Part | What it covers | Changes when |
|---|---|---|
| [fabric/](fabric/) | the simulator: file formats, routing functions, options | the C++ changes |
| [systems/](systems/) | one page per system family: what is known, how it is modelled, what is assumed | we learn more about a system |
| [studies/](studies/) | one page per question, with setup, results and caveats | never: a study is a dated snapshot |

## Simulator

- [fabric/formats.md](fabric/formats.md): topology, routing table, source route and traffic matrix files
- [fabric/routing.md](fabric/routing.md): routing functions and why each is deadlock-free
- [fabric/options.md](fabric/options.md): configuration keys

## Systems

| Page | Systems | Status |
|---|---|---|
| [systems/tpu-torus.md](systems/tpu-torus.md) | TPU Ironwood (TPU7), TPU 8t | Ironwood modelled; 8t assumed to share its layout |
| [systems/tpu-boardfly.md](systems/tpu-boardfly.md) | TPU 8i | modelled |
| Trainium | AWS Trn2, Trn3 | planned |
| NVLink | NVIDIA NVL72 and later | planned |

## Studies

See [studies/README.md](studies/README.md).

## Conventions

**Confidence.** Every number on a system page carries one of three labels:

| Label | Meaning |
|---|---|
| published | stated by the vendor or in a paper; cited |
| derived | follows from published numbers; the derivation is shown |
| assumed | a modelling choice where nothing is published; the reasoning is given, and the page says whether results depend on it |

**One source per number.** Calibration constants (link rates, latencies)
live in the exporter that writes the topology; pages refer to it instead of
copying values.

**Public sources only.** Pages cite vendor documentation, papers, talks and
public photos. Nothing is taken from reverse engineering vendor software.

**Reproducible studies.** A study records the fork commit, the command or
script that produced every number, and the raw results.

## Adding a system

1. Copy [systems/_template.md](systems/_template.md) to `systems/<family>.md`.
2. Write an exporter in `utils/fabric/` that produces the topology (and
   tables or routes) in the formats above, with the calibration constants at
   its top; `fabricio.py` has the writers.
3. Fill in the assumptions table, including how much each assumption moves
   the results.
4. Add a row to the systems table above.
