# Message contracts (TCL-005)

Shared, service-neutral contract artefacts. Docker images copy only one app
directory, so tests that read this folder skip with a reason under `make test-*`;
CI and local runs from a full checkout use it.

## `schemas/scms.execution.event.v1.schema.json`
The OES -> MiniTOS execution event envelope as decided in the TCL-002 backend
review (section 2.7) and vault decision T-006. `terminal.id` is required because
MiniTOS `apply_execution_message` reads it.

## `golden/scms.execution.event.v1/*.json`
One full leg (`leg_dispatched` ... `leg_completed`) exactly as the real OES outbox
publisher (`publish_outbox._publish_batch`) puts it on the wire: topic, QoS,
retain flag and JSON payload. Only `event_id` and `data.occurred_at` vary per run;
the producer test ignores those two fields when comparing.

- Producer test: `apps/terminal-oes/execution/test_contract_execution_events.py`
  fails if OES output drifts from the files.
- Consumer test: `apps/minitos/minitos_core/test_contract_execution_events.py`
  feeds the files to the real MiniTOS consumer. It rebinds only id **values**
  (order, leg, CHE, position, site, terminal) to its own database and never adds
  or removes keys, so a missing field stays missing.

## `golden/scms.execution.event.v1/handoff/*.json`
TCL-003a hand-off events, written from the real OES producer driven through
TCL-003a's own scenario (`execution/test_handoffs.py`): `container_transferred`
QC->TT and TT->RTG (T-006) and `handoff_exception` with reason
`SIMULATOR_FAILURE` and `detail` "no TT within 300 s" (T-007/D-013, D-017; D-017
supersedes `NO_TT_TIMEOUT`). `data.handoff_id` is also ignored when comparing (derived from the
session pk). Producer test `execution/test_contract_handoff_events.py`, consumer
test `minitos_core/test_contract_handoff_events.py`; both skip until TCL-003a is
merged. The schema requires `handoff_id`, `from_che_id`, `to_che_id` (and the
D-015 names) on `container_transferred`, and `handoff_id`, `reason`, `detail` (and
the names) on `handoff_exception`, where `reason` is the D-017 enum
(`SIMULATOR_FAILURE` today). MiniTOS rejects a missing reason (`reason_required`)
or an unknown one (`reason_unknown`) and accepts a missing `detail`
(`allOf`/`if`/`then`, supported by the stdlib validator in the OES test).

The goldens were regenerated from TCL-003a (baf3adf4aff5): every envelope carries
`terminal {id, code}`, `container_picked_up` carries `data.container_visit_id`, and
events carry `che_name` (D-015); hand-off events also carry `from_che_name` and
`to_che_name` (required there). The MiniTOS consumer rejects a `container_visit_id`
that is not the order's own visit, so the consumer tests rebind it too.

## `golden/scms.execution.event.v1/failure/*.json`
`leg_failed` written by the real OES producer after a Godot `failed` result on the
Sparkplug result path: `01_leg_failed_with_detail` (Godot sent
`Command/Result/Detail`, so OES puts it in `data.detail`) and
`02_leg_failed_without_detail` (empty detail, so the key is left out).
`data.reason` is Godot's raw `Command/Result/Reason` code. `data.detail` is an
optional free string in the schema. The detail behaviour arrives with TCL-028
(PR #20); `01` was generated on TCL-028 5e36047. Producer test
`execution/test_contract_failure_events.py`: the with-detail check is skipped while
TCL-028 is not merged and fails under `SCMS_CONTRACT_STRICT=1`. Consumer test
`minitos_core/test_contract_failure_events.py` replays `leg_dispatched`,
`leg_started` and each `leg_failed` through MiniTOS. Regenerate (on a branch with
TCL-028) with
`SCMS_UPDATE_GOLDEN=1 python manage.py test execution.test_contract_failure_events`.

Regenerate after an intended producer change, then review the diff:

    cd apps/terminal-oes
    SCMS_UPDATE_GOLDEN=1 python manage.py test execution.test_contract_execution_events execution.test_contract_handoff_events

Known-bug tests are marked `expectedFailure`; set `SCMS_CONTRACT_STRICT=1` to see
the real failures. When a bug is fixed the test reports "unexpected success":
remove its marker in the same change.
