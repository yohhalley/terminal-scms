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

Regenerate after an intended producer change, then review the diff:

    cd apps/terminal-oes
    SCMS_UPDATE_GOLDEN=1 python manage.py test execution.test_contract_execution_events

Known-bug tests are marked `expectedFailure`; set `SCMS_CONTRACT_STRICT=1` to see
the real failures. When a bug is fixed the test reports "unexpected success":
remove its marker in the same change.
