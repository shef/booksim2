# Studies

A study answers one question and is a dated snapshot: once written it is not
updated, apart from dated follow-up sections that add a measurement, and a
newer study supersedes it. Each one records:

- the question;
- the systems and slice sizes, linking to their `systems/` pages;
- the fork commit and the exact commands or scripts behind every number;
- results, with raw data alongside;
- caveats, including which assumptions the conclusion depends on.

Name files `YYYY-MM-<topic>.md`.

| Study | Question | Status |
|---|---|---|
| Collectives at 1,024 chips | How do all-reduce and all-to-all compare on an Ironwood torus and an 8i Boardfly, fault-free and with one OCS failure? | pending |
| Boardfly under failures | How much throughput does each failure cost, by slice size? | pending |
| [NSDI 2024 Table 2](2026-10-nsdi-table2.md) | Do our torus model and fault routes reproduce NSDI's all-to-all throughput under one OCS failure? | done (2026-10; fixed-bytes follow-up 2026-10-29) |
