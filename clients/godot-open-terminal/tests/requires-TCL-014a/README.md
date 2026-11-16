# D-012 / D-013 acceptance

QA acceptance tests for D-012 (QC appear + seeded hold, wait for a late TT)
and D-013 (TT no-show time-out). They use the landed TCL-014a hooks (SimClock,
SimRandom, `qc_discharge`) and a FakeSparkplugManager (no MQTT broker).

The main suite pins the `fast` timing profile and asserts hold bounds from the
configured profile values, not hard-coded 10..30 s. A shorter realistic-profile
variant does the same with the realistic hold and no-show parameters. The QC
deposit event is `handoff_completed`; device ids are MiniTOS CHE names
(`QC01`, `TT01`).

Wired into `make test-godot` and the `godot` CI job (fast profile, no broker).
