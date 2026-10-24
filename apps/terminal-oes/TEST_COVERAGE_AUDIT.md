# Terminal OES test coverage audit

Audit date: 2026-10-19

The Django suite currently discovers 31 tests in `execution/tests.py`.

Covered contracts:

- MQTT retry behavior plus retained Sparkplug Host Application ONLINE/OFFLINE state and will.
- Dependency-free Sparkplug protobuf metric encode/decode compatibility.
- Idempotent released-order ingestion and Site-scoped identity.
- Transactional assignment, binary DCMD outbox publication, expiry, and command transitions.
- Authenticated APIs and the operator equipment console.
- Idempotent Site creation, checksummed snapshot retrieval, stale-revision rejection,
  and v2-only OES subscriptions.
- Ready-only simulation start, one active session per Site, restricted runner claim,
  short-lived bootstrap token, frozen bootstrap, stop flow, and console access.
- NBIRTH/DBIRTH binding, powered-off DDATA semantics, command-result idempotency,
  NDEATH invalidation, dispatch blocking, and isolation between Sites that reuse
  local Location and CHE IDs.

Additional cross-process checks:

- `apps/simulator-runner/test_runner.py` verifies no process starts without a
  claim and that the launcher accepts only the fixed image and environment allowlist.
- Godot `tests/unit/test_terminal_integration.gd` verifies Sparkplug lifecycle,
  DCMD idempotency, same-session restart recovery, telemetry cadence, and movement.
- Godot `tests/unit/test_che_rails.gd` verifies rail-constrained CHE behavior.
- Godot `tests/integration/test_local_mqtt_broker.gd` verifies broker delivery of
  NBIRTH, DBIRTH, powered-off DDATA, explicit DDEATH, and NDEATH.

Remaining integration gaps:

- The live broker test does not yet boot MiniTOS, OES, the host runner, and Godot
  as one black-box fixture. The component contract tests cover each boundary,
  while deployment smoke testing must still prove a complete real session.
- Broker ACL/TLS behavior and malicious Sparkplug publisher rejection are not covered.
- Runner crash recovery before it can report status needs a host-level watchdog test.
- Traffic-aware dispatch, reservations, and multi-CHE handoff execution remain future features.
