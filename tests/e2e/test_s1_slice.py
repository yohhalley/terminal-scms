"""Unit checks for the TCL-007 e2e runner's matching and timing logic.

No broker, Docker or services needed:
    python3 -m unittest discover -s tests/e2e -p 'test_*.py'
"""
import time
import unittest
from datetime import datetime, timezone

import s1_slice as s1

LEGS = [{"id": 11, "sequence": 1}, {"id": 12, "sequence": 2}, {"id": 13, "sequence": 3}]


def released(order_id=1, legs=LEGS):
    return s1.UnsMessage(
        f"scms/v2/e/s/tos/orders/{order_id}/released",
        {"schema": "scms.order.released.v1", "data": {"order": {"id": order_id}, "legs": legs}},
        time.monotonic(),
    )


def execution(event, leg_id=None, che=None, order_id=1, **extra):
    data = {"event": event, "order_id": order_id, **extra}
    if leg_id is not None:
        data["leg_id"] = leg_id
    if che is not None:
        data["che_name"] = che
    return s1.UnsMessage(
        f"scms/v2/e/s/oes/orders/{order_id}/execution",
        {"schema": "scms.execution.event.v1", "data": data},
        time.monotonic(),
    )


def make_stream(*messages):
    stream = s1.EventStream()
    stream.learn_order(released())
    for m in messages:
        stream.push(m)
    return stream


def step(limit=0.3):
    return s1.StepResult(step="x", title="t", trigger="simulator", expected=[], limit_s=limit)


class MatchTests(unittest.TestCase):
    def test_in_order_passes(self):
        stream = make_stream(
            execution("leg_dispatched", 11, "QC01"),
            execution("leg_dispatched", 12, "TT02"),
        )
        r = s1.run_expectations(step(), stream, [
            s1.Expect("leg_dispatched", leg=1, che="QC01"),
            s1.Expect("leg_dispatched", leg=2, che="TT*"),
        ], time.monotonic())
        self.assertTrue(r.passed, r.error)
        self.assertEqual(r.observed, ["leg_dispatched(leg 1, QC01)", "leg_dispatched(leg 2, TT02)"])

    def test_wrong_order_fails_strictly(self):
        stream = make_stream(
            execution("leg_dispatched", 12, "TT02"),
            execution("leg_dispatched", 11, "QC01"),
        )
        r = s1.run_expectations(step(), stream, [
            s1.Expect("leg_dispatched", leg=1, che="QC01"),
            s1.Expect("leg_dispatched", leg=2, che="TT*"),
        ], time.monotonic())
        self.assertFalse(r.passed)
        self.assertIn("order/content mismatch", r.error)

    def test_missing_event_times_out_at_the_limit(self):
        stream = make_stream(execution("leg_started", 11, "QC01"))
        started = time.monotonic()
        r = s1.run_expectations(step(limit=0.2), stream, [
            s1.Expect("leg_started", leg=1, che="QC01"),
            s1.Expect("container_picked_up", leg=1, che="QC01"),
        ], started)
        self.assertFalse(r.passed)
        self.assertIn("timeout", r.error)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_late_event_fails_even_if_it_arrives(self):
        late = execution("leg_started", 11, "QC01")  # arrives now
        stream = make_stream()
        start = time.monotonic() - 1.0  # the step started 1 s ago, limit 0.2 s
        stream.push(late)
        r = s1.run_expectations(step(limit=0.2), stream, [s1.Expect("leg_started", leg=1)], start)
        self.assertFalse(r.passed)
        self.assertIn("timeout", r.error)
        self.assertIn("arrived at", r.error)

    def test_on_time_event_read_late_still_passes(self):
        msg = execution("leg_started", 11, "QC01")
        msg.received_at = time.monotonic() - 0.9   # arrived 0.1 s into the step
        stream = make_stream(msg)
        start = time.monotonic() - 1.0              # the runner reads it 1 s later
        r = s1.run_expectations(step(limit=0.2), stream, [s1.Expect("leg_started", leg=1)], start)
        self.assertTrue(r.passed, r.error)
        self.assertAlmostEqual(r.elapsed_s, 0.1, delta=0.05)

    def test_failure_event_is_reported(self):
        stream = make_stream(execution(
            "handoff_exception", 11, "QC01", reason="SIMULATOR_FAILURE", detail="no TT within 300 s",
        ))
        r = s1.run_expectations(step(), stream, [s1.Expect("container_transferred")], time.monotonic())
        self.assertFalse(r.passed)
        self.assertIn("failure event", r.error)
        self.assertIn("SIMULATOR_FAILURE", r.error)

    def test_other_orders_and_schemas_are_ignored(self):
        stream = make_stream(
            execution("leg_dispatched", 99, "QC01", order_id=7),
            s1.UnsMessage("scms/v2/e/s/tos/site/created", {"schema": "scms.site.created.v1"}, time.monotonic()),
            execution("leg_dispatched", 11, "QC01"),
        )
        r = s1.run_expectations(step(), stream, [s1.Expect("leg_dispatched", leg=1, che="QC01")], time.monotonic())
        self.assertTrue(r.passed, r.error)
        self.assertEqual(len(stream.ignored), 2)

    def test_visit_status_changed_events_are_outside_the_strict_order(self):
        # PR #37 (TCL-003b): a reset publishes one creation event per seeded
        # visit (carrier visit -> Arrived, container visit -> Preadvised) and
        # visit-in-operation one Arrived -> InOperation event, all before the
        # release; execution can add container-visit changes between legs.
        # They are not S1-slice business events: ignored, never "extra".
        def status(kind, vid, frm, to, source="minitos.scenario"):
            return s1.UnsMessage(
                f"scms/v2/e/s/tos/{kind}/{vid}/status-changed",
                {"schema": "scms.visit.status_changed.v1",
                 "data": {"from_status": frm, "to_status": to, "source": source}},
                time.monotonic(),
            )
        stream = make_stream(
            status("carrier-visits", 1, None, "Arrived"),
            status("container-visits", 1, None, "Preadvised"),
            status("carrier-visits", 1, "Arrived", "InOperation"),
            execution("leg_dispatched", 11, "QC01"),
            status("container-visits", 1, "Preadvised", "OnEquipment", source="oes.execution"),
            execution("leg_dispatched", 12, "TT02"),
        )
        r = s1.run_expectations(step(), stream, [
            s1.Expect("leg_dispatched", leg=1, che="QC01"),
            s1.Expect("leg_dispatched", leg=2, che="TT*"),
        ], time.monotonic())
        self.assertTrue(r.passed, r.error)
        self.assertEqual(len(stream.ignored), 4)
        self.assertIsNone(stream.next_message(time.monotonic() + 0.05))

    def test_handoff_names(self):
        msg = execution("container_transferred", 12, "TT01", from_che_name="QC01", to_che_name="TT01")
        self.assertIsNone(s1.match_error(s1.Expect("container_transferred", from_che="QC01", to_che="TT*"), msg, {12: 2}))
        self.assertIsNotNone(s1.match_error(s1.Expect("container_transferred", from_che="TT*", to_che="RTG01"), msg, {12: 2}))

    def test_released_payload_needs_three_legs(self):
        self.assertIsNone(s1._released_has_three_legs(released()))
        self.assertIn("legs 1..3", s1._released_has_three_legs(released(legs=LEGS[:2])))


class PlanTests(unittest.TestCase):
    def test_plan_matches_spec_order(self):
        events = [e.label() for _, _, _, exps in s1.s1_plan(True) for e in exps]
        self.assertEqual(events, [
            "order.released()",
            "leg_dispatched(leg 1, QC01)", "leg_dispatched(leg 2, TT*)",           # AC3.4 1-2
            "leg_started(leg 1, QC01)", "container_picked_up(leg 1, QC01)",         # AC3.4 3-4
            "leg_started(leg 2, TT*)", "container_transferred(QC01->TT*)",          # AC3.4 5-6
            "leg_completed(leg 1, QC01)",                                           # AC3.4 7
            "leg_dispatched(leg 3, RTG01)", "leg_started(leg 3, RTG01)",            # AC4.3 1-2
            "container_transferred(TT*->RTG01)", "leg_completed(leg 2, TT*)",       # AC4.3 3-4
            "container_dropped(leg 3, RTG01)", "leg_completed(leg 3, RTG01)",       # AC4.3 5-6
        ])
        self.assertEqual(sum(1 for e in events if e.startswith("container_transferred")), 2)
        self.assertEqual(sum(1 for e in events if e.startswith("container_picked_up")), 1)

    def test_without_step4(self):
        self.assertEqual([s for s, *_ in s1.s1_plan(False)], ["3a", "3b", "3c", "3d"])

    def test_step_3d_title_uses_the_active_profile(self):
        titles = {step: title for step, title, *_ in s1.s1_plan(False, profile="realistic")}
        self.assertEqual(titles["3d"], "QC01 -> TT hand-off (Godot, realistic timings)")
        default = {step: title for step, title, *_ in s1.s1_plan(False)}
        self.assertEqual(default["3d"], "QC01 -> TT hand-off (Godot, fast timings)")

    def test_default_limit_is_d037(self):
        self.assertEqual(s1.parse_args([]).step_limit, 5.0)


class ReportTests(unittest.TestCase):
    def test_markdown_marks_fail_and_skip(self):
        ok = s1.StepResult("3a", "Release", "cli", ["order.released()"], passed=True, elapsed_s=0.4)
        bad = s1.StepResult("3b", "Dispatch", "http", ["x"], error="timeout: x")
        skip = s1.StepResult("3c", "Pick", "simulator", ["y"], error="skipped: an earlier step failed")
        md = s1.render_markdown([ok, bad, skip], ["note"])
        self.assertIn("| 3a Release | PASS |", md)
        self.assertIn("| 3b Dispatch | FAIL |", md)
        self.assertIn("| 3c Pick | SKIP |", md)
        self.assertIn("**Overall: FAIL**", md)


class ClockTests(unittest.TestCase):
    """D-037 / D-050: the clock starts when the trigger is published and stops
    when the step's last expected event is received. Reaction steps use the
    wall-clock limit; 3d/4 are asserted in sim time."""

    def make_run(self, sim_offsets, step_limit=5.0, sim_stamps=None):
        import argparse
        args = argparse.Namespace(
            oes_url="http://127.0.0.1:1", compose="true", include_step4=False,
            step_limit=step_limit, scenario="import_v1",
            time_scale=10.0, timing_profile="fast",
        )
        run = s1.SliceRun(args)
        run.validator = None
        run.nbirth_metrics = s1.nbirth_fixture(
            profile="fast", qc_hold=(2.0, 5.0), rtg_hold=(2.0, 5.0),
        )
        self.issued = {}

        def push(offset_base, offset, message):
            message.received_at = offset_base + offset
            run.stream.push(message)
            return message

        def trigger_release(step):
            self.issued["release"] = time.monotonic()
            time.sleep(0.05)  # the CLI call itself takes time; it is inside the clock
            self.released = push(self.issued["release"], 0.10, released())
            return True

        def trigger_dispatch(step):
            t = self.issued["dispatch"] = time.monotonic()
            push(t, 0.02, execution("leg_dispatched", 11, "QC01"))
            self.dispatched_last = push(t, 0.03, execution("leg_dispatched", 12, "TT02"))
            # Default envelope stamps: 3c at t=0, 3d ends at 28.5 s sim (TT02 drive).
            stamps = sim_stamps or [
                "2026-10-25T00:00:00+00:00",
                "2026-10-25T00:00:00+00:00",
                "2026-10-25T00:00:00+00:00",
                "2026-10-25T00:00:28.500000+00:00",
                "2026-10-25T00:00:28.500000+00:00",
            ]
            events = [
                execution("leg_started", 11, "QC01", occurred_at=stamps[0]),
                execution("container_picked_up", 11, "QC01", occurred_at=stamps[1]),
                execution("leg_started", 12, "TT02", occurred_at=stamps[2]),
                execution(
                    "container_transferred", 12,
                    from_che_name="QC01", to_che_name="TT02", occurred_at=stamps[3],
                ),
                execution("leg_completed", 11, "QC01", occurred_at=stamps[4]),
            ]
            for offset, message in zip(sim_offsets, events):
                push(t, offset, message)
            return True

        run.trigger_release = trigger_release
        run.trigger_dispatch = trigger_dispatch
        run.find_oes_order = lambda: 1
        run.check_redispatch_noop = lambda step: None
        run.final_checks = lambda: None
        return run

    def by_id(self, run):
        return {r.step: r for r in run.results}

    def test_clock_starts_at_trigger_and_stops_at_last_expected_event(self):
        run = self.make_run([0.53, 0.54, 1.04, 1.05, 1.06])
        run.run_steps()
        r = self.by_id(run)
        self.assertTrue(all(x.passed for x in run.results), [x.error for x in run.results])
        # 3a: the clock started no later than the release command was issued.
        self.assertGreaterEqual(r["3a"].elapsed_s, 0.10 - 0.001)
        self.assertLess(r["3a"].elapsed_s, 0.10 + 0.05)
        self.assertEqual(r["3a"].clock_from, "release command issued")
        # 3b: from the dispatch request to the second leg_dispatched.
        self.assertAlmostEqual(r["3b"].elapsed_s, 0.03, delta=0.02)
        self.assertEqual(r["3b"].clock_from, "dispatch-next request sent")
        # 3c/3d: chained on the exact receipt time of the previous step's last
        # event, not on when the runner happened to read it.
        self.assertAlmostEqual(r["3c"].elapsed_s, 0.54 - 0.03, delta=0.0011)
        self.assertAlmostEqual(r["3d"].elapsed_s, 1.06 - 0.54, delta=0.0011)
        self.assertAlmostEqual(r["3c"].last_received_at, self.dispatched_last.received_at + 0.51, delta=1e-9)
        self.assertIn("leg_dispatched(leg 2, TT02) received (end of step 3b)", r["3c"].clock_from)

    def test_reaction_step_over_the_wall_limit_fails(self):
        # 3c is reaction-only (D-050 / D-037): last event 0.32 s after 3c start, limit 0.3 s.
        # 3c starts at the second leg_dispatched (0.03 s after dispatch).
        run = self.make_run([0.20, 0.35, 1.00, 1.10, 1.20], step_limit=0.3)
        run.run_steps()
        r = self.by_id(run)
        self.assertTrue(r["3b"].passed, r["3b"].error)
        self.assertFalse(r["3c"].passed)
        self.assertIn("timeout: container_picked_up", r["3c"].error)
        self.assertIn("arrived at 0.320 s", r["3c"].error)
        self.assertTrue(r["3d"].error.startswith("skipped"))

    def test_physical_step_is_not_failed_by_the_5s_wall_limit(self):
        # 3d's last event arrives 0.31 s after 3c ended; a 0.3 s D-037 wall
        # limit must not fail it. The wait uses the D-050 safety timeout.
        run = self.make_run([0.05, 0.06, 0.20, 0.30, 0.37], step_limit=0.3)
        run.run_steps()
        r = self.by_id(run)
        self.assertTrue(r["3c"].passed, r["3c"].error)
        self.assertTrue(r["3d"].passed, r["3d"].error)
        self.assertFalse(r["3d"].wall_asserted)
        self.assertGreater(r["3d"].limit_s, 0.3)
        self.assertAlmostEqual(r["3d"].sim_elapsed_s, 28.5, places=3)
        self.assertLessEqual(r["3d"].sim_elapsed_s, r["3d"].sim_bound_s)

    def test_physical_steps_use_safety_timeout_and_sim_bound(self):
        run = self.make_run([0.53, 0.54, 1.04, 1.05, 1.06])
        run.run_steps()
        r = self.by_id(run)
        self.assertEqual(r["3a"].limit_s, 5.0)
        self.assertTrue(r["3a"].wall_asserted)
        self.assertEqual(r["3b"].limit_s, 5.0)
        self.assertEqual(r["3c"].limit_s, 5.0)
        self.assertTrue(r["3c"].wall_asserted)
        self.assertFalse(r["3d"].wall_asserted)
        self.assertGreater(r["3d"].limit_s, 5.0)
        self.assertAlmostEqual(r["3d"].sim_bound_s, 28.704, places=3)

    def test_simulator_step_without_a_passed_trigger_is_not_timed(self):
        run = self.make_run([0.53, 0.54, 1.04, 1.05, 1.06])
        plan = [p for p in s1.s1_plan(False) if p[2] not in ("minitos-release", "oes-dispatch")]
        s1_plan = s1.s1_plan
        s1.s1_plan = lambda include_step4, profile="fast": plan
        try:
            run.run_steps()
        finally:
            s1.s1_plan = s1_plan
        self.assertFalse(run.results[0].passed)
        self.assertIn("no triggering event", run.results[0].error)


class SchemaTests(unittest.TestCase):
    def test_golden_events_validate_if_jsonschema_is_installed(self):
        validator = s1.load_execution_validator()
        if validator is None:
            self.skipTest("jsonschema not installed")
        import json
        golden = s1.REPO_ROOT / "contracts/golden/scms.execution.event.v1"
        files = sorted(golden.glob("*.json")) + sorted((golden / "handoff").glob("*.json"))
        self.assertTrue(files)
        for path in files:
            wire = json.loads(path.read_text())
            msg = s1.UnsMessage(wire["topic"], wire["payload"], 0.0)
            self.assertIsNone(s1.schema_check(validator, msg), path.name)
        broken = s1.UnsMessage("t", {"schema": "scms.execution.event.v1", "data": {"event": "leg_started"}}, 0.0)
        self.assertIsNotNone(s1.schema_check(validator, broken))



def sim_execution(event, occurred_at, leg_id=11, che="QC01", **extra):
    return execution(event, leg_id, che, occurred_at=occurred_at, **extra)


class StartSimulationTests(unittest.TestCase):
    def test_http_201_is_accepted(self):
        body = {"id": "sess-1", "time_scale": 10, "seed": 1}
        self.assertEqual(s1.accept_start_simulation(201, body, 10.0), body)

    def test_http_200_is_accepted_when_scale_matches(self):
        body = {"id": "sess-1", "time_scale": 10.0, "seed": 1}
        self.assertEqual(s1.accept_start_simulation(200, body, 10), body)

    def test_http_200_is_accepted_when_scale_matches_as_string(self):
        body = {"id": "sess-1", "time_scale": "10"}
        self.assertEqual(s1.accept_start_simulation(200, body, 10.0), body)

    def test_http_200_with_mismatched_scale_is_rejected(self):
        body = {"id": "sess-1", "time_scale": 1.0, "seed": 1}
        with self.assertRaises(RuntimeError) as cm:
            s1.accept_start_simulation(200, body, 10.0)
        self.assertIn("HTTP 200", str(cm.exception))
        self.assertIn("time_scale 1.0", str(cm.exception))
        self.assertIn("requested 10", str(cm.exception))

    def test_http_200_without_time_scale_is_rejected(self):
        with self.assertRaises(RuntimeError) as cm:
            s1.accept_start_simulation(200, {"id": "sess-1"}, 10.0)
        self.assertIn("time_scale None", str(cm.exception))

    def test_other_http_status_is_rejected(self):
        with self.assertRaises(RuntimeError) as cm:
            s1.accept_start_simulation(500, {"detail": "boom"}, 10.0)
        self.assertIn("HTTP 500", str(cm.exception))

    def test_session_note_logs_scale_and_seed(self):
        note = s1.session_setup_note(
            {"id": "abc", "time_scale": 10, "seed": 1}, created=True,
        )
        self.assertEqual(note, "Simulation session abc created (time_scale 10, seed 1)")
        reused = s1.session_setup_note(
            {"id": "abc", "time_scale": 10, "seed": None}, created=False,
        )
        self.assertEqual(reused, "Simulation session abc reused (time_scale 10, seed None)")


class WorstStepTests(unittest.TestCase):
    def test_sim_elapsed_chains_on_previous_step_end(self):
        pick = "2026-10-25T00:00:25.300000+00:00"
        transfer = "2026-10-25T00:00:54.500000+00:00"
        drop = "2026-10-25T00:01:16.300000+00:00"

        c = s1.StepResult("3c", "QC pick", "simulator", [], elapsed_s=0.51, limit_s=5)
        s1.record_sim_time(c, sim_execution("leg_started", pick))
        s1.record_sim_time(c, sim_execution("container_picked_up", pick))

        d = s1.StepResult("3d", "H1", "simulator", [], elapsed_s=2.522, limit_s=5)
        s1.record_sim_time(d, sim_execution("leg_started", "2026-10-25T00:00:26+00:00", 12, "TT02"))
        s1.record_sim_time(d, sim_execution(
            "container_transferred", transfer, 12, "TT02",
            from_che_name="QC01", to_che_name="TT02",
        ))

        four = s1.StepResult("4", "H2", "oes-dispatch", [], elapsed_s=2.547, limit_s=5)
        s1.record_sim_time(four, sim_execution("leg_started", "2026-10-25T00:01:01.800000+00:00", 13, "RTG01"))
        s1.record_sim_time(four, sim_execution("container_dropped", drop, 13, "RTG01"))

        # 3a/3b have wall-clock occurred_at only and must not break the chain.
        a = s1.StepResult("3a", "Release", "cli", [], elapsed_s=1.46, limit_s=5, passed=True)
        b = s1.StepResult("3b", "Dispatch", "http", [], elapsed_s=0.35, limit_s=5, passed=True)
        s1.annotate_sim_elapsed([a, b, c, d, four])
        self.assertIsNone(a.sim_elapsed_s)
        self.assertIsNone(b.sim_elapsed_s)
        self.assertEqual(c.sim_elapsed_s, 0.0)
        # 3d is the TT drive, from TT leg_started (00:00:26) to transfer
        # (00:00:54.500) = 28.5 s, not from the QC pick 0.7 s earlier.
        self.assertEqual(d.sim_elapsed_s, 28.5)
        self.assertEqual(four.sim_elapsed_s, 21.8)

        line = s1.worst_step_summary([a, b, c, d, four])
        self.assertEqual(
            line,
            "Worst step (wall-clock): 4 H2 2.547 s / limit 5 s; sim time 21.8 s.",
        )

    def test_dispatch_occurred_at_is_not_treated_as_sim_time(self):
        message = execution("leg_dispatched", 11, "QC01", occurred_at="2026-11-15T07:38:24.691469+00:00")
        self.assertIsNone(s1.message_sim_datetime(message))

    def test_worst_step_without_sim_time_omits_it(self):
        a = s1.StepResult("3a", "Release", "cli", [], elapsed_s=1.46, limit_s=5)
        b = s1.StepResult("3b", "Dispatch", "http", [], elapsed_s=0.35, limit_s=5)
        self.assertEqual(
            s1.worst_step_summary([a, b]),
            "Worst step (wall-clock): 3a Release 1.46 s / limit 5 s.",
        )

    def test_markdown_includes_the_worst_step_line(self):
        ok = s1.StepResult("3d", "H1", "simulator", ["x"], passed=True, elapsed_s=2.522, limit_s=5)
        s1.record_sim_time(ok, sim_execution("container_transferred", "2026-10-25T00:00:54.500000+00:00", 12, "TT02"))
        s1.record_sim_time(ok, sim_execution("leg_started", "2026-10-25T00:00:26+00:00", 12, "TT02"))
        # record_sim_time is called in arrival order in production; first then last.
        # Here last_sim_at would be the later call. Re-set in chronological order:
        ok.first_sim_at = datetime(2026, 10, 25, 0, 0, 26, tzinfo=timezone.utc)
        ok.last_sim_at = datetime(2026, 10, 25, 0, 0, 54, 500000, tzinfo=timezone.utc)
        md = s1.render_markdown([ok], [])
        self.assertIn("Worst step (wall-clock): 3d H1 2.522 s / limit 5 s; sim time 28.5 s.", md)
        self.assertIn("**Overall: PASS**", md)


class D050BoundsTests(unittest.TestCase):
    """Layout-derived sim-time bounds for physical-work steps 3d and 4."""

    def setUp(self):
        self.fast_nbirth = s1.nbirth_fixture(
            profile="fast", qc_hold=(2.0, 5.0), rtg_hold=(2.0, 5.0),
        )
        self.bounds = s1.load_physical_bounds(
            s1.REPO_ROOT, nbirth=self.fast_nbirth, profile="fast", time_scale=10,
        )

    def test_import_v1_layout_points_and_fast_holds(self):
        b = self.bounds
        self.assertEqual((b.qc_lane.x, b.qc_lane.y), (150.0, 240.0))
        self.assertEqual((b.block_tl.x, b.block_tl.y), (122.5, 166.0))
        self.assertEqual((b.tt_starts["TT01"].x, b.tt_starts["TT01"].y), (60.0, 150.0))
        self.assertEqual((b.tt_starts["TT02"].x, b.tt_starts["TT02"].y), (80.0, 150.0))
        self.assertEqual(b.speed_mps, 4.0)
        self.assertEqual(b.qc_hold, (2.0, 5.0))
        self.assertEqual(b.rtg_hold, (2.0, 5.0))
        self.assertEqual(b.rtg_positioning_s, 0.0)
        self.assertEqual(b.step_ms, 100)
        self.assertAlmostEqual(b.slack_s, 0.2, places=9)

    def test_step_3d_bound_is_max_of_drive_and_qc_hold(self):
        b = self.bounds
        # Parallel: deposit at max(hold end, TT ready). Fast hold max 5 s is
        # shorter than either TT drive, so the bound is drive + slack.
        self.assertAlmostEqual(b.bound_3d_s("TT02"), 28.50438562747845 + 0.2, places=5)
        self.assertAlmostEqual(b.bound_3d_s("TT01"), 31.81980515339464 + 0.2, places=5)
        self.assertEqual(b.bound_3d_s(), b.bound_3d_s("TT01"))  # worst case
        self.assertIn("TT02 drive 114.0 m", b.formula_3d("TT02"))
        self.assertIn("QC hold <= 5 s", b.formula_3d("TT02"))
        self.assertIn("+ 2 ticks (0.2 s)", b.formula_3d("TT02"))
        self.assertNotIn("≤", b.formula_3d("TT02"))
        self.assertNotIn("→", b.formula_3d("TT02"))

    def test_step_4_bound_is_max_of_drive_and_positioning_plus_hold(self):
        b = self.bounds
        # fast, prompt dispatch, P=0: max(drive + 2 ticks, 2 ticks + 0) + hold
        # = drive + 0.2 + 5, same numeric value as the pre-M2 formula.
        self.assertAlmostEqual(b.bound_4_s(), 19.736150207170596 + 0.2 + 5.0, places=5)
        self.assertIn("RTG hold <= 5 s", b.formula_4())
        self.assertIn("RTG positioning 0 s", b.formula_4())
        self.assertIn("QC01-L1.01->BLOCK01-TL.01", b.formula_4())
        self.assertNotIn("gantry", b.formula_4())
        self.assertNotIn("≤", b.formula_4())
        self.assertNotIn("→", b.formula_4())

    def test_safety_timeout_is_scaled_bound_plus_slack_not_5s(self):
        b = self.bounds
        safety = b.safety_wall_s("3d", "TT02")
        self.assertGreater(safety, 5.0)
        self.assertAlmostEqual(safety, b.bound_3d_s("TT02") / 10 + 30.0, places=5)

    def test_bounds_follow_nbirth_holds_not_a_hard_coded_120(self):
        # Pre-TCL-034e hold shape (90-120 / 60-120), with RtgPositioningS 0
        # since that metric is now required (F1). The expected bound is
        # derived from the same metrics, so the D-051 cycle (60 s hold) does
        # not require a literal 120.0 here.
        metrics = s1.nbirth_fixture(
            profile="realistic", qc_hold=(90.0, 120.0), rtg_hold=(60.0, 120.0),
            positioning_s=0.0,
        )
        timings = s1.timings_from_nbirth(metrics)
        realistic = s1.load_physical_bounds(
            s1.REPO_ROOT, nbirth=metrics, profile="realistic", time_scale=10,
        )
        self.assertEqual(timings["rtg_positioning_s"], 0.0)
        self.assertAlmostEqual(
            realistic.bound_3d_s("TT01"),
            max(realistic.tt_drive_to_qc_s("TT01")[0], timings["qc_hold"][1]) + realistic.slack_s,
            places=5,
        )
        self.assertAlmostEqual(
            realistic.bound_4_s(),
            max(
                realistic.drive_s(realistic.qc_lane, realistic.block_tl) + realistic.slack_s,
                realistic.slack_s + timings["rtg_positioning_s"],
            ) + timings["rtg_hold"][1],
            places=5,
        )

    def test_bounds_follow_d051_nbirth_holds_and_positioning(self):
        # TCL-034e / D-051 / D-058: QC hold 60, RTG hold 30, positioning 30.
        metrics = s1.nbirth_fixture(
            profile="realistic", qc_hold=(60.0, 60.0), rtg_hold=(30.0, 30.0),
            positioning_s=30.0,
        )
        timings = s1.timings_from_nbirth(metrics)
        realistic = s1.load_physical_bounds(
            s1.REPO_ROOT, nbirth=metrics, profile="realistic", time_scale=10,
        )
        # 3d: max(TT01 drive ~31.8, 60) + 2 ticks = 60.2
        self.assertAlmostEqual(
            realistic.bound_3d_s("TT01"),
            max(realistic.tt_drive_to_qc_s("TT01")[0], timings["qc_hold"][1]) + realistic.slack_s,
            places=5,
        )
        self.assertAlmostEqual(realistic.bound_3d_s("TT01"), 60.0 + 0.2, places=5)
        # L=0: max(19.736+0.2, 0.2+30) + 30 = 60.2
        self.assertAlmostEqual(
            realistic.bound_4_s(),
            max(19.736150207170596 + 0.2, 0.2 + 30.0) + 30.0,
            places=5,
        )
        self.assertAlmostEqual(realistic.bound_4_s(), 60.2, places=5)

    def test_slack_is_two_configured_ticks(self):
        metrics = s1.nbirth_fixture(
            profile="fast", qc_hold=(2.0, 5.0), rtg_hold=(2.0, 5.0), step_ms=200,
        )
        bounds = s1.load_physical_bounds(
            s1.REPO_ROOT, nbirth=metrics, profile="fast", time_scale=10,
        )
        self.assertEqual(bounds.step_ms, 200)
        self.assertAlmostEqual(bounds.slack_s, 0.4, places=9)
        self.assertIn("+ 2 ticks (0.4 s)", bounds.formula_3d("TT02"))
        self.assertAlmostEqual(bounds.bound_3d_s("TT02"), 28.50438562747845 + 0.4, places=5)

    def test_env_speed_override_is_used(self):
        faster = s1.load_physical_bounds(
            s1.REPO_ROOT, nbirth=self.fast_nbirth, profile="fast", time_scale=10,
            env={"SIMULATOR_DEFAULT_SPEED_MPS": "8"},
        )
        self.assertEqual(faster.speed_mps, 8.0)
        self.assertAlmostEqual(faster.bound_3d_s("TT02"), 28.50438562747845 / 2 + 0.2, places=5)

    def test_timings_from_nbirth_require_hold_metrics(self):
        with self.assertRaisesRegex(ValueError, "QcHoldMaxS"):
            s1.timings_from_nbirth({"Sim/Timing/QcHoldMinS": 2})

    def test_timings_from_nbirth_require_rtg_positioning(self):
        """F1: RtgPositioningS is required (tcl-002.4), not defaulted to 0."""
        metrics = s1.nbirth_fixture(
            profile="fast", qc_hold=(2.0, 5.0), rtg_hold=(2.0, 5.0),
            include_positioning=False,
        )
        with self.assertRaisesRegex(
            ValueError, "missing required metric Sim/Timing/RtgPositioningS",
        ):
            s1.timings_from_nbirth(metrics)
        with self.assertRaisesRegex(ValueError, "missing required metric Sim/Timing/RtgPositioningS"):
            s1.load_physical_bounds(s1.REPO_ROOT, nbirth=metrics, profile="fast", time_scale=10)
        metrics[s1.NBIRTH_POSITIONING] = None
        with self.assertRaisesRegex(ValueError, "missing required metric Sim/Timing/RtgPositioningS"):
            s1.timings_from_nbirth(metrics)
        metrics[s1.NBIRTH_POSITIONING] = "thirty"
        with self.assertRaisesRegex(ValueError, "RtgPositioningS is not a number"):
            s1.timings_from_nbirth(metrics)
        metrics[s1.NBIRTH_POSITIONING] = -1
        with self.assertRaisesRegex(ValueError, "RtgPositioningS must be >= 0"):
            s1.timings_from_nbirth(metrics)
        metrics[s1.NBIRTH_POSITIONING] = 0
        self.assertEqual(s1.timings_from_nbirth(metrics)["rtg_positioning_s"], 0.0)

    def test_step_3d_excludes_the_command_start_gap(self):
        """CI run 43977160552: pick 26.900, TT start 27.500, transfer 56.000.

        Chaining from the QC pick would be 29.1 s and fail the 28.704 s
        drive bound. The layout bound is the drive, from TT leg_started.
        """
        c = s1.StepResult("3c", "QC pick", "simulator", [], passed=True)
        s1.record_sim_time(c, sim_execution(
            "container_picked_up", "2026-10-25T00:00:26.900000+00:00",
        ))
        d = s1.StepResult("3d", "H1", "simulator", [], passed=True, wall_asserted=False)
        s1.record_sim_time(d, sim_execution(
            "leg_started", "2026-10-25T00:00:27.500000+00:00", 12, "TT02",
        ))
        s1.record_sim_time(d, sim_execution(
            "leg_completed", "2026-10-25T00:00:56+00:00", 11, "QC01",
        ))
        s1.apply_sim_time_assertions([c, d], self.bounds, tt_name="TT02")
        self.assertTrue(d.passed, d.error)
        self.assertAlmostEqual(d.sim_elapsed_s, 28.5, places=3)
        self.assertLessEqual(d.sim_elapsed_s, d.sim_bound_s)

    def test_sim_time_within_bound_passes(self):

        c = s1.StepResult("3c", "QC pick", "simulator", [], elapsed_s=0.51, limit_s=5, passed=True)
        s1.record_sim_time(c, sim_execution("container_picked_up", "2026-10-25T00:00:00+00:00"))
        d = s1.StepResult(
            "3d", "H1", "simulator", [], elapsed_s=2.52, limit_s=32.87,
            passed=True, wall_asserted=False,
        )
        s1.record_sim_time(d, sim_execution("leg_started", "2026-10-25T00:00:00+00:00", 12, "TT02"))
        s1.record_sim_time(d, sim_execution(
            "leg_completed", "2026-10-25T00:00:28.500000+00:00", 11, "QC01",
        ))
        s1.apply_sim_time_assertions([c, d], self.bounds, tt_name="TT02")
        self.assertTrue(d.passed, d.error)
        self.assertAlmostEqual(d.sim_elapsed_s, 28.5, places=3)
        self.assertTrue(any("D-050 sim time 28.5 s / bound 28.704 s" in x for x in d.checks))

    def test_sim_time_over_the_bound_fails(self):
        c = s1.StepResult("3c", "QC pick", "simulator", [], passed=True)
        s1.record_sim_time(c, sim_execution("container_picked_up", "2026-10-25T00:00:00+00:00"))
        d = s1.StepResult("3d", "H1", "simulator", [], passed=True, wall_asserted=False)
        s1.record_sim_time(d, sim_execution("leg_started", "2026-10-25T00:00:00+00:00", 12, "TT02"))
        s1.record_sim_time(d, sim_execution(
            "leg_completed", "2026-10-25T00:00:40+00:00", 11, "QC01",
        ))
        s1.apply_sim_time_assertions([c, d], self.bounds, tt_name="TT02")
        self.assertFalse(d.passed)
        self.assertIn("sim time exceeds layout bound", d.error)
        self.assertIn("40 s", d.error)
        self.assertIn("28.704 s", d.error)

    def test_step_4_assertion_uses_the_block_drive_plus_hold(self):
        d = s1.StepResult("3d", "H1", "simulator", [], passed=True)
        s1.record_sim_time(d, sim_execution(
            "leg_completed", "2026-10-25T00:00:28.500000+00:00", 11, "QC01",
        ))
        four = s1.StepResult("4", "H2", "oes-dispatch", [], passed=True, wall_asserted=False)
        s1.record_sim_time(four, sim_execution(
            "leg_started", "2026-10-25T00:00:28.500000+00:00", 13, "RTG01",
        ))
        s1.record_sim_time(four, sim_execution(
            "container_dropped", "2026-10-25T00:00:50.300000+00:00", 13, "RTG01",
        ))
        s1.apply_sim_time_assertions([d, four], self.bounds)
        self.assertTrue(four.passed, four.error)
        self.assertAlmostEqual(four.sim_elapsed_s, 21.8, places=3)
        self.assertAlmostEqual(four.sim_bound_s, 24.936, places=3)

    def test_step_4_bound_absorbs_dispatch_latency(self):
        """A 25 s sim (2.5 s wall at x10) dispatch delay is not a layout fail."""
        d = s1.StepResult("3d", "H1", "simulator", [], passed=True)
        s1.record_sim_time(d, sim_execution(
            "leg_completed", "2026-10-25T00:00:28.500000+00:00", 11, "QC01",
        ))
        four = s1.StepResult("4", "H2", "oes-dispatch", [], passed=True, wall_asserted=False)
        s1.record_sim_time(four, sim_execution(
            "leg_started", "2026-10-25T00:00:53.500000+00:00", 13, "RTG01",
        ))
        s1.record_sim_time(four, sim_execution(
            "container_dropped", "2026-10-25T00:00:58.600000+00:00", 13, "RTG01",
        ))
        s1.apply_sim_time_assertions([d, four], self.bounds)
        self.assertTrue(four.passed, four.error)
        self.assertAlmostEqual(four.sim_elapsed_s, 30.1, places=3)
        # max(19.936, 25 + 0.2 + 0) + 5 = 30.2: the RTG picks one tick after
        # leg_started, so the 2-tick arrival slack leaves one tick of margin
        # (F2; with one tick this case measured exactly at the bound).
        self.assertAlmostEqual(four.sim_bound_s, 30.2, places=3)
        self.assertIn("(RTG leg_started - H1) 25 s + 2 ticks (0.2 s)", four.sim_bound_formula)

    def test_step_4_dispatch_wall_over_the_limit_fails(self):
        four = s1.StepResult(
            "4", "H2", "oes-dispatch", ["leg_dispatched(leg 3, RTG01)"],
            passed=True, wall_asserted=False,
            dispatch_wall_s=5.2, dispatch_limit_s=5.0,
        )
        s1.apply_dispatch_wall_assertion(four)
        self.assertFalse(four.passed)
        self.assertIn("dispatch reaction exceeds 5 s wall-clock limit", four.error)

    def test_step_4_dispatch_wall_within_the_limit_is_logged(self):
        four = s1.StepResult(
            "4", "H2", "oes-dispatch", ["leg_dispatched(leg 3, RTG01)"],
            passed=True, wall_asserted=False,
            dispatch_wall_s=0.35, dispatch_limit_s=5.0,
        )
        s1.apply_dispatch_wall_assertion(four)
        self.assertTrue(four.passed, four.error)
        self.assertTrue(any("dispatch reaction 0.35 s / wall limit 5 s" in c for c in four.checks))

    def test_markdown_includes_sim_columns_and_physical_summary(self):
        ok = s1.StepResult(
            "3d", "H1", "simulator", ["x"], passed=True, elapsed_s=2.522,
            limit_s=32.87, wall_asserted=False, sim_bound_s=28.704,
        )
        s1.record_sim_time(ok, sim_execution("leg_started", "2026-10-25T00:00:00+00:00", 12, "TT02"))
        s1.record_sim_time(ok, sim_execution(
            "leg_completed", "2026-10-25T00:00:28.500000+00:00", 11, "QC01",
        ))
        # record_sim_time is last-wins for last_sim_at; first then last as in production.
        md = s1.render_markdown([ok], [])
        self.assertIn("| Wall (s) | Wall limit (s) | Sim (s) | Sim bound (s) |", md)
        self.assertIn("| 3d H1 | PASS | 2.522 | safety 32.87 | 28.5 | 28.704 |", md)
        self.assertIn(
            "Worst step (wall-clock): 3d H1 2.522 s / safety wait 32.87 s (not asserted); sim time 28.5 s.",
            md,
        )
        self.assertIn("Physical steps (D-050, sim time vs layout bound): 3d 28.5 / 28.704 s.", md)


class NbirthAndTickTests(unittest.TestCase):
    def test_sparkplug_roundtrip_holds(self):
        from sparkplug_codec import decode_payload, encode_payload, metrics_by_name

        payload = encode_payload([
            {"name": "Sim/Timing/QcHoldMinS", "type": "Int64", "value": 2},
            {"name": "Sim/Timing/QcHoldMaxS", "type": "Int64", "value": 5},
            {"name": "Sim/Timing/RtgHoldMinS", "type": "Int64", "value": 2},
            {"name": "Sim/Timing/RtgHoldMaxS", "type": "Int64", "value": 5},
            {"name": "Sim/Timing/RtgPositioningS", "type": "Int64", "value": 30},
            {"name": "Sim/FixedStepMs", "type": "Int64", "value": 100},
            {"name": "Sim/Timing/Profile", "type": "String", "value": "fast"},
            {"name": "Sim/TimeScale", "type": "Double", "value": 10.0},
        ], timestamp_ms=1)
        metrics = metrics_by_name(decode_payload(payload))
        timings = s1.timings_from_nbirth(metrics)
        self.assertEqual(timings["qc_hold"], (2.0, 5.0))
        self.assertEqual(timings["rtg_hold"], (2.0, 5.0))
        self.assertEqual(timings["rtg_positioning_s"], 30.0)
        self.assertEqual(timings["step_ms"], 100)
        self.assertEqual(timings["profile"], "fast")
        self.assertEqual(timings["time_scale"], 10.0)

    def test_parse_fixed_step_ms_prefers_env_then_simclock(self):
        self.assertEqual(
            s1.parse_fixed_step_ms("", "const DEFAULT_STEP_MS := 100", env={}),
            100,
        )
        self.assertEqual(
            s1.parse_fixed_step_ms("", "const DEFAULT_STEP_MS := 100", env={"SIMULATOR_FIXED_STEP_MS": "200"}),
            200,
        )


if __name__ == "__main__":
    unittest.main()
