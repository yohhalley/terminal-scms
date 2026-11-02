"""Unit checks for the TCL-007 e2e runner's matching and timing logic.

No broker, Docker or services needed:
    python3 -m unittest discover -s tests/e2e -p 'test_*.py'
"""
import time
import unittest

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
    """D-037: the clock starts when the trigger is published and stops when
    the step's last expected event is received."""

    def make_run(self, sim_offsets, step_limit=5.0):
        import argparse
        args = argparse.Namespace(
            oes_url="http://127.0.0.1:1", compose="true", include_step4=False,
            step_limit=step_limit, scenario="import_v1",
        )
        run = s1.SliceRun(args)
        run.validator = None
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
            events = [
                execution("leg_started", 11, "QC01"), execution("container_picked_up", 11, "QC01"),
                execution("leg_started", 12, "TT02"),
                execution("container_transferred", 12, from_che_name="QC01", to_che_name="TT02"),
                execution("leg_completed", 11, "QC01"),
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

    def test_simulator_step_over_the_limit_fails(self):
        # 3d's last event arrives 0.31 s after 3c ended with a 0.3 s limit.
        run = self.make_run([0.05, 0.06, 0.20, 0.30, 0.37], step_limit=0.3)
        run.run_steps()
        r = self.by_id(run)
        self.assertTrue(r["3c"].passed, r["3c"].error)
        self.assertFalse(r["3d"].passed)
        self.assertIn("timeout: leg_completed", r["3d"].error)
        self.assertIn("arrived at 0.310 s", r["3d"].error)

    def test_simulator_step_without_a_passed_trigger_is_not_timed(self):
        run = self.make_run([0.53, 0.54, 1.04, 1.05, 1.06])
        plan = [p for p in s1.s1_plan(False) if p[2] not in ("minitos-release", "oes-dispatch")]
        s1_plan = s1.s1_plan
        s1.s1_plan = lambda include_step4: plan
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


if __name__ == "__main__":
    unittest.main()
