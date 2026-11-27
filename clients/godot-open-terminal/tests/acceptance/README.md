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

D-059 / D-078: a no-show is reported once per time-out period (default 300 s),
not once only. The suite derives an "once in this period" window from the
configured time-out (period − max(hold spread, 5 s) − 5 s; 290 s at the
default 300 s) and then asserts that the first D-059 repeat arrives just past
first report + period, in both the fast and the realistic arm.

## Run time

Local, Godot 4.2.1, fast profile, via `run-godot-test.sh`, after the S1
window and positive-control change (3 runs): 20.7 s, 20.4 s, 20.0 s.
Breakdown from a timed run in the same session: D-012a 7.0 s (250 sim runs),
D-012b 9.4 s (328 sim runs), D-013 once-per-period 0.39 s, realistic variant
1.64 s (includes hold samples and an early-TT check). The widened window plus
the positive control added 0.16 s to the fast no-show arm and 0.26 s to the
realistic variant versus the 60 s window at `45ca577` (still well under 1 s
each).

CI, at `45ca577` (60 s window, before S1): run 45968480338, godot job
140402812674, acceptance step 32 s. That figure is not a measurement of the
widened window.
