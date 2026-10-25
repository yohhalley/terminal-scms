# Tests that need TCL-014a

QA acceptance tests for D-012 (QC appear + seeded 10-30 s hold, wait for a late
TT) and D-013 (TT no-show time-out). They use the TCL-014a hooks (SimClock,
SimRandom, `qc_discharge`) and do not compile on main, so they are not wired
into `make test-godot`. Verified green against TCL-014a at cf21733.
Move them into `tests/unit/` and the Makefile when TCL-014a merges.
