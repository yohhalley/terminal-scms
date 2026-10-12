# Dispatch and Yard Planning Domain Model

## Recommendation

Keep four kinds of data separate:

1. **Physical truth**: terminal geometry, slots, roads, transfer points, and equipment capabilities.
2. **Operational policy**: CHE ranges, pool allocations, dispatch policy, and maintenance windows.
3. **Work state**: work instructions, executable handling steps, assignments, and execution events.
4. **Planning state**: candidate yard positions, reservations, scores, and optimizer runs.

The optimizer should consume these models, not own them. Hard constraints first produce feasible
`(handling step, position, CHE, route, time)` candidates. A rolling-horizon solver then chooses among
those candidates using travel, lateness, congestion, rehandle, workload-balance, and plan-stability costs.

Do not assign CHE or pools directly to every yard slot. That creates a large and fragile relation that
must be rewritten whenever a block changes. Assign coverage to blocks, zones, or bay/row ranges and
derive slot eligibility through containment. A slot-level exception can be added later if a real use case
requires it.

## Current Model Audit

### Keep

- `Location`, `Area`, `Block`, `Pile`, and `Position` form a useful physical hierarchy.
- `RoutingNode`, `Road`, and `TerminalConnection` are a reasonable MVP routing graph.
- `EquipmentType` and `Che` correctly separate fleet-class capabilities from equipment instances.
- `ServiceCargoRoute` and `VisitCargoRoute` are not duplicates: one is a template and one is an
  operational visit-specific realization. Their relationship should be made explicit.
- `PileBooking` is useful as a coarse stacking allocation, but its name should eventually describe the
  cargo group or planning allocation it reserves capacity for.

### Redundant or conflated state

- `Order` is already the beginning of a `WorkInstruction`; retaining both concepts later would duplicate
  the same unit of work. Rename it when API compatibility permits.
- `OrderLeg.che` conflates an executable step with a mutable equipment assignment. Move the CHE link to
  a separate `WorkAssignment` so an assignment can be proposed, accepted, rejected, cancelled, or
  reassigned without rewriting work definition.
- `Position.is_occupied` duplicates `container_visit is not null`. Make occupancy derived. Replace the
  `is_reserved` boolean with a reservation record containing owner, purpose, and expiry.
- `Pile.tiers` overlaps the generated `Position` records and `Block.max_tier`. Pick generated positions as
  inventory truth and treat the block value as grid configuration.
- `Che.current_block` is derivable from geographic position and area containment. It may remain as a
  denormalized cache, but must not be authoritative.
- `EquipmentType.running_hours` is instance state on `Che`, not a property of an equipment class.
  Maintenance intervals belong on `EquipmentType`; accumulated operating hours belong on the CHE or a
  meter-reading history.
- `PointOfWork.related_ches` will duplicate pool membership and pool-to-POW allocation. Remove it after
  those models exist, retaining an explicit temporary override only if operations need one.
- `Area.ROAD` and `Road` can both describe a road. Use `Road` as centerline/routing truth and reserve
  `Area` for surface polygons. There is also a duplicated `TERMINAL_AREA` constant in `Area`.
- `POWConnection` and the routing graph look redundant unless their meanings are made distinct. Keep
  `Road`/`RoutingNode` for physical travel. Keep a renamed `ProcessDependency` only if it represents
  production flow or handoff precedence rather than travel.
- `Location` currently represents terminals and mobile carrier types. This is acceptable for the MVP,
  but a later split into `Facility` and `CarrierVisit` will avoid mixed lifecycle rules.

## Models to Add

### Equipment allocation

- `EquipmentPool`: terminal, name, equipment-class rules, active flag.
- `PoolMembership`: pool, CHE, valid-from/to, priority. Membership is time-bounded and auditable.
- `PoolPOWAllocation`: pool, POW, valid-from/to, priority, target/min/max quantity, dispatch policy.
- `MaintenanceWindow`: CHE, planned start/end, actual start/end, type, state.
- `CheMeterReading`: CHE, timestamp, running hours, fuel/energy, source. Keep a current snapshot on CHE
  only as a cache.

### Reachability and operating envelopes

- `CheRange`: terminal, name, validity, priority, active flag.
- `CheRangeBlockSegment`: range, block, bay-from/to, row-from/to, allowed actions.
- `CheRangeArea`: range, area, allowed actions.
- `CheRangeCheAssignment` and `CheRangePoolAssignment`: explicit time-bounded subjects. These two small
  tables are clearer and safer than a Django generic foreign key.
- `ServicePoint`: routing node plus an operational kind such as QC handoff, transfer zone, truck lane,
  rail slot, block entry, or parking stand.
- `ServicePointRequirement`: required CHE capability, access mode, load state, clearance, or interface.

This handles the described equipment patterns without giving each one a bespoke dispatch system:

- ARMG transfer zones become service points with `live-load` or `decoupled-bed` handoff modes.
- RTG transfer points can be one service point per bay.
- Shuttle-carrier lanes can be one point per row with clearance restrictions.
- Reach stacker/top handler/forklift working faces become service points outside a block.
- STS and RMG rail limits belong to graph edges and equipment movement profiles, not CHE ranges.

### Work execution

- `WorkInstruction`: container visit, move kind, from/to service or inventory positions, priority,
  sequence, release/due times, planning status, and work queue.
- `HandlingStep`: ordered phase of a work instruction (`travel_empty`, `pickup`, `travel_laden`,
  `handoff`, `set_down`, `wait`). It references service points and required capability, not a CHE.
- `WorkAssignment`: handling step, CHE, planned/accepted/start/end times, state, score, assignment source.
- `WorkEvent`: append-only execution events for arrival, pickup, set-down, abort, and completion.
- `WorkQueuePOWAllocation`: assigns queues to POWs with sequence, priority, and validity.

An order that crosses an automated block boundary is then a sequence of steps and handoffs. The truck
and ARMG may have separate assignments while both contribute to one work instruction.

### Yard planning

- `StackingRule`: cargo predicates and allowed/excluded areas, blocks, bay/row ranges, and tiers.
- `PositionReservation`: position, container/cargo group, purpose, status, valid-until, optimizer run.
- `PlacementCandidate`: optimizer run, container, position, feasibility result, cost components.
- `PlanningRun`: horizon, input snapshot/version, solver status, objective components, created/committed
  timestamps.

Keep candidate and committed state separate. A selected candidate becomes a reservation; it does not
become inventory until the physical set-down completes.

## One Feasibility Pipeline

For each released work instruction:

1. Generate yard-position candidates from cargo and stacking constraints.
2. Expand each candidate into the required handling steps and transfer interfaces.
3. Filter CHE by capability, current state, maintenance, pool/POW allocation, CHE range, and graph access.
4. Find feasible routes and estimate empty/laden travel plus service and handoff time.
5. Optimize the short horizon across both position and CHE assignment decisions.
6. Commit near-term assignments; retain later decisions as revisable plans.
7. Re-run when inventory, equipment availability, ETA, or execution events materially change.

This is closer to alternating/decomposed optimization than Expectation Maximization. EM estimates hidden
statistical variables; here both decisions are explicit and constrained. For an MVP, alternate yard
candidate scoring and equipment assignment for two or three iterations. For a short horizon, an
integrated CP-SAT model can choose both directly. Use deterministic feasibility rules outside the solver
so infeasible assignments can never be rescued by a low objective score.

## MVP Sequence

1. Rename concepts in code comments/API aliases first; avoid a disruptive table rename.
2. Add equipment pools, memberships, POW allocations, CHE ranges, and maintenance windows.
3. Add service points and connect them to the existing routing graph.
4. Introduce `HandlingStep` and `WorkAssignment`; migrate `OrderLeg.che` into assignments.
5. Replace position booleans with derived occupancy and explicit reservations.
6. Add a deterministic eligibility service with explainable rejection reasons.
7. Add planning runs and placement candidates, then introduce a rolling-horizon solver.
8. Remove deprecated direct relations only after APIs and simulator telemetry use the new model.

## External Patterns Worth Borrowing

- Open-RMF separates task descriptions, runtime execution, and robot allocation, and models tasks as
  ordered phases/events: https://github.com/open-rmf/rmf_task
- VDA 5050 uses maps, nodes/edges, and zone sets with movement rules rather than per-destination robot
  permissions: https://www.vda.de/dam/jcr%3A09f03b91-13e2-4db3-bf30-4f221710071b/VDA5050_EN.pdf
- OR-Tools distinguishes assignment, scheduling, routing, and network flow while allowing CP-SAT to
  combine heterogeneous constraints: https://developers.google.com/optimization/cp/
- ISA-95 distinguishes operations schedules, work schedules, job orders, and execution responses:
  https://www.isa.org/intech-home/2017/january-february/features/advancements-in-isa-95

The Navis references supplied with this project reinforce the same split: CHE ranges and pools constrain
eligibility; POW settings shape policy; work instructions and job stages track execution; travel,
look-ahead, congestion, and productivity values are optimizer parameters rather than physical models.
