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

## Run time (TCL-037 N5)

About 20 s locally (23.8 s before D-012b was trimmed; CI took 36 s at #45).
D-012a (250 sim runs, 7 s) and D-012b (328 sim runs, 9 s) take most of it; one
sim run costs about 25 ms, mostly stepping. D-012a keeps all 250 samples because
it is the distribution check (both bounds must occur). "Reported once" watches
60 s of sim time past the no-show time-out, which adds about 0.1 s in total; it
must stay below the D-059 repeat period, which is the time-out (300 s by default).
