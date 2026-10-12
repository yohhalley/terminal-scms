# MiniTOS Data Flow & Architecture Quick Reference

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MiniTOS Terminal Operating System                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐         ┌────────────────────────┐                    │
│  │  EDI Processing  │ ──────→ │ Planning & Optimization│ ──────→ ┌──────────┤
│  │                  │         │ • Yard Assignment      │        │ Dispatch │
│  │ • Parse EDI      │         │ • Container Placement  │        │ System   │
│  │ • Create Visits  │         │ • Route Calculation    │        │          │
│  │ • Bulk Import    │         │ • TimeEstimation       │        └──────────┤
│  └──────────────────┘         └────────────────────────┘              ▲     │
│           │                                                            │     │
│           ▼                                                            │     │
│  ┌──────────────────┐                                          ┌───────────┐│
│  │ TransportVisit   │◄─────────────────────────────────────────│ CHE Tasks ││
│  │ • EDI Manifest   │    Order Leg Completion Feedback         │ • Accept  ││
│  │ • Container List │    (pickup, delivery, fault)             │ • Reject  ││
│  │ • Bay Sequence   │                                          │ • Complete││
│  └──────────────────┘                                          └───────────┘│
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                       TOS Database (Django ORM)                       │   │
│  │  • Location ↔ Block ↔ Pile ↔ Position (spatial hierarchy)           │   │
│  │  • Che ↔ CheCurrentTask ↔ OrderLeg ↔ Order (execution tracking)      │   │
│  │  • YardCargoCategory ↔ YardPositionAssignment (placement rules)      │   │
│  │  • PointOfWork ↔ POWConnection (routing network)                    │   │
│  │  • DispatchingPolicy ↔ ChePool (dispatch config)                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                              │                                    ▲
                              │                                    │
                    Dispatch Orders                   Status Updates & Telemetry
                    (via MQTT/API)                    (via MQTT/API)
                              │                                    │
                              ▼                                    │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Godot Simulator (Equipment Execution)                     │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │    RTGs      │  │    STS/QCs   │  │  TractorsT   │  │  Containers  │   │
│  │              │  │              │  │              │  │              │   │
│  │ • Position   │  │ • Position   │  │ • Position   │  │ • Position   │   │
│  │ • Status     │  │ • Status     │  │ • Status     │  │ • Status     │   │
│  │ • Fuel Level │  │ • Fuel Level │  │ • Fuel Level │  │ • Location   │   │
│  │ • Spreader   │  │ • Spreader   │  │ • Spreader   │  │  Updates     │   │
│  │ • Load Wt    │  │ • Load Wt    │  │ • Load Wt    │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │           MQTT Broker (SparkPlugB Protocol via MES)                 │   │
│  │  Topics: /minitos/{location}/che/status                            │   │
│  │          /minitos/{location}/che/telemetry                         │   │
│  │          /minitos/{location}/dispatch/order-assigned               │   │
│  │          /minitos/{location}/order/complete                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │
                         ┌─────────────▼──────────────┐
                         │     MES (Ignition)         │
                         │  • Telemetry aggregation   │
                         │  • Equipment metrics       │
                         │  • Efficiency tracking     │
                         │  • Fault detection         │
                         └────────────────────────────┘
```

---

## Data Flow: Simplified EDI → Visit Creation → Bulk Container Import → Planning

### Sequence Diagram: Simplified Workflow (Sprint 1.1)

```
User/EDI      TOS (MiniTOS)           MQTT              Godot Simulator
  │                 │                   │                      │
  │─── Upload EDI ─→│                   │                      │
  │                 │                   │                      │
  │                 ├─ Parse & Validate │                      │
  │                 ├─ Create Visit     │                      │
  │                 └─ 50 ContainerVisits                      │
  │                 │                   │                      │
  │◄─── ACK ────────│                   │                      │
  │  (TransportVisit created)           │                      │
  │                 │                   │                      │
  │─── Click Plan ─→│                   │                      │
  │                 │                   │                      │
  │                 ├─ Classify containers (YCC)               │
  │                 ├─ Search positions                        │
  │                 ├─ Optimize placement                      │
  │                 ├─ Create 5 WorkQueues (by bay)            │
  │                 ├─ Sequence 50 Orders (time: 0→100 min)   │
  │                 ├─ Calculate routes (50→150 OrderLegs)    │
  │                 │                   │                      │
  │◄─── List ──────│ 5 WorkQueues       │                      │
  │  (ready, 50 orders pending)         │                      │
  │                 │                   │                      │
  │(Dispatch Cycle, every 2 minutes)    │                      │
  │                 │                   │                      │
  │                 ├─ Find available CHE                      │
  │                 ├─ Score & select (NearestFirst)           │
  │                 ├─ Create CheCurrentTask                   │
  │                 └─ 10 OrderLegs assigned                   │
  │                 │                   │                      │
  │                 ├──── Dispatch x10 ─→ OrderLeg JSON        │
  │                 │                   │                      │
  │                 │                   │─→ RTG#1 receives     │
  │                 │                   │   order #1           │
  │                 │                   │                      │
  │                 │                   │←─ RTG#1 acknowledges │
  │                 │←─ Accept Task ────│                      │
  │                 │                   │                      │
  │                 │                   │←─ Pickup confirmed ──│
  │                 │◄─ Pickup Confirm ─│   (time T1)          │
  │                 │  (update OrderLeg)│                      │
  │                 │                   │                      │
  │                 │                   │←─ Delivery confirmed ┤
  │                 │◄─ Delivery Confirm◄─  (time T2)          │
  │                 │  (mark OrderLeg Done)                    │
  │                 │                   │                      │
  │                 ├─ Check if Order complete                 │
  │                 │  (all OrderLegs done?)                   │
  │                 │                   │                      │
  │                 ├─ Update Order status                     │
  │                 │                   │                      │
  │                 └─ Progress: 1/50 complete                 │
  │                 │                   │                      │
  │(Repeat every 2 mins for next 10 legs, for ~50 iterations) │
  │                 │                   │                      │
  │                 │(at T=100 minutes)                        │
  │                 │                   │                      │
  │                 ├─ All 50 orders complete                  │
  │                 ├─ Visit status: InOperation → Complete    │
  │                 │                   │                      │
  │◄─── Dashboard ──│ 50/50 ✓           │                      │
  │  (vessel discharge completed)                              │
```

---

## Container Lifecycle: State Transitions

```
┌─────────────────────────────────────────────────────────────────┐
│              ContainerVisit Status State Machine                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│     ┌──────────┐       ┌──────┐       ┌────────┐               │
│     │Preadvised├─────→ │Arrived│      │AtYard  │               │
│     └──────────┘       └──────┘       └────────┘               │
│          ▲                 │                │                   │
│          │                 │                ▼                   │
│          │                 │            ┌────────┐              │
│          │                 ▼            │ AtQC   │              │
│          │             (2 hrs)          └────────┘              │
│          │                                  │                   │
│          │                                  ▼                   │
│          │                            ┌──────────┐              │
│          │                            │OnVessel  │              │
│          │                            └──────────┘              │
│          │                                  │                   │
│    Manual Override                          ▼                   │
│    (if error)                          ┌──────────┐              │
│          │◄─────────────────────────────│Departed  │              │
│          │                             └──────────┘              │
│          │                                  │                   │
│          └──────────────────────────────────►                   │
│                                             ▼                   │
│                                        ┌──────────┐              │
│                                        │Completed │              │
│                                        └──────────┘              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Trigger Points:
  Preadvised→Arrived:  Manual (visit arrives at terminal)
  Arrived→AtYard:      Automatic (OrderLeg pickup confirmed)
  AtYard→AtQC:         Automatic (OrderLeg delivery confirmed to QC1 POW)
  AtQC→OnVessel:       Automatic (OrderLeg delivery confirmed to QC POW)
  OnVessel→Departed:   Automatic (visit eta time passes OR manual trigger)
  *→Completed:        Manual (archive record)
```

---

## Order Decomposition: Order → OrderLegs

```
┌─────────────────────────────────────────────────────────────────┐
│              Order Decomposition Logic                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  INPUT: Order                                                    │
│    from_position = [Block1, Bay01, Tier01]                      │
│    to_position   = [QC1]                                        │
│    est_duration  = 100 seconds (productivity-based)             │
│                                                                  │
│  STEP 1: Map positions to POWs                                  │
│    from_position ──→ Block1 POW                                 │
│    to_position   ──→ QC1 POW                                    │
│                                                                  │
│  STEP 2: Find shortest path (Dijkstra)                          │
│    Block1 ──→ Block1_TP ──→ QC1_TP ──→ QC1                     │
│     (Yard)    (Transfer)   (Transfer)  (Quay)                   │
│                                                                  │
│  STEP 3: Segment into logical legs                              │
│    Leg 1: Block1 → Block1_TP  [Yard Equipment: RTG/ASC]        │
│    Leg 2: Block1_TP → QC1_TP  [Transfer Eq: TT/SC]             │
│    Leg 3: QC1_TP → QC1        [Quay Equipment: STS]            │
│                                                                  │
│  STEP 4: Create OrderLeg per segment                            │
│    OrderLeg(leg_num=1, che=null, distance=100m, est_time=60s)  │
│    OrderLeg(leg_num=2, che=null, distance=500m, est_time=120s) │
│    OrderLeg(leg_num=3, che=null, distance=50m,  est_time=40s)  │
│                                                                  │
│  OUTPUT: 3 OrderLegs [Pending, Pending, Pending]                │
│    Ready for dispatching (one leg at a time)                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Dispatch Algorithm: CHE Selection Logic

```
┌──────────────────────────────────────────────────────────────────┐
│          CHE Dispatching Algorithm (Every 2 minutes)             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  INPUT: 10 unassigned OrderLegs (from DispatchingQueue)         │
│         DispatchingPolicy (NearestFirst, weights: d=0.5, u=0.3) │
│                                                                  │
│  FOR EACH OrderLeg:                                             │
│    ┌─── Step 1: Find available CHE ───────────────────┐         │
│    │ Filter by:                                       │         │
│    │  • Status = Idle (not Working, Breakdown, etc.)  │         │
│    │  • is_available = True                           │         │
│    │  • No active CheCurrentTasks                     │         │
│    │  • Equipment type matches leg requirement        │         │
│    │  • Can reach start POW from current location     │         │
│    │ Result: available_che_list = [RTG1, RTG2, ASC1] │         │
│    └────────────────────────────────────────────────────┘        │
│                                                                  │
│    ┌─── Step 2: Score each CHE ──────────────────────┐         │
│    │ For each CHE in available_che_list:             │         │
│    │   distance_to_start = 150m                       │         │
│    │   distance_score = 1 - (150/2000) = 0.925       │         │
│    │                                                  │         │
│    │   pool_avg_load = 0.5 tasks/che                 │         │
│    │   che_load = 0 tasks                            │         │
│    │   util_delta = (0.5 - 0) / 0.5 = 1.0            │         │
│    │   util_score = 0.5 + (1.0 * 0.5) = 1.0         │         │
│    │                                                  │         │
│    │   fuel_level = 75%                              │         │
│    │   fuel_score = 0.75                             │         │
│    │                                                  │         │
│    │   final_score = 0.5*0.925 + 0.3*1.0 + 0.2*0.75 │         │
│    │              = 0.463 + 0.300 + 0.150 = 0.913    │         │
│    │                                                  │         │
│    │ Scores: {RTG1: 0.913, RTG2: 0.802, ASC1: 0.756} │         │
│    └────────────────────────────────────────────────────┘        │
│                                                                  │
│    ┌─── Step 3: Select best CHE ──────────────────────┐         │
│    │ Sort by score descending                        │         │
│    │ Select: RTG1 (score=0.913)                      │         │
│    └────────────────────────────────────────────────────┘        │
│                                                                  │
│    ┌─── Step 4: Create assignment ────────────────────┐         │
│    │ order_leg.che = RTG1                            │         │
│    │ DispatchingQueue.status = Assigned              │         │
│    │ CheCurrentTask.status = Assigned                │         │
│    │ Send MQTT: order_leg JSON to RTG1               │         │
│    │ RTG1 status: Idle → Working                     │         │
│    └────────────────────────────────────────────────────┘        │
│                                                                  │
│  END FOR                                                        │
│                                                                  │
│  OUTPUT: All 10 OrderLegs assigned (or marked "NoCHEAvailable") │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Yard Planning: Container Classification & Placement

```
┌──────────────────────────────────────────────────────────────────┐
│        Yard Planning: Classification & Position Selection        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  INPUT: ContainerVisit (container attributes)                   │
│    iso_type = 40ft                                              │
│    requires_power = True (reefer)                               │
│    is_imdg = False                                              │
│    pod = "SGSIN"                                                │
│    category = Import                                            │
│                                                                  │
│  STEP 1: Classify to YCC Hierarchy                              │
│    ┌─► Check Leaf YCCs (specific) first:                       │
│    │   • "Reefer 40ft Import" ──→ requires_power=T, iso=40ft  │
│    │     Matches! confidence=0.95                              │
│    │   Done (use most specific)                                │
│    │                                                           │
│    │   (If no match, backtrack to parent YCC)                  │
│    │                                                           │
│    └─ Result: YCC = "Reefer 40ft Import"                       │
│                                                                  │
│  STEP 2: Search Available Positions                             │
│    Query: YardPositionAssignment                               │
│      .filter(ycc="Reefer 40ft Import")                         │
│      .filter(effective_from <= now)                            │
│    Eligible piles: [Block3_Bay02, Block3_Bay04]  (even=40ft)  │
│                                                                  │
│    Per pile, find available positions:                         │
│    Block3_Bay02:                                               │
│      ├─ Tier1: occupied (Container X)                          │
│      ├─ Tier2: occupied (Container Y)                          │
│      ├─ Tier3: available ✓ (no gaps below)                     │
│      ├─ Tier4: available ✓                                     │
│                                                                  │
│    Block3_Bay04:                                               │
│      ├─ Tier1: occupied (Container Z)                          │
│      ├─ Tier2: available ✓                                     │
│                                                                  │
│    Available positions: [Block3.02.1.03, Block3.02.1.04,       │
│                          Block3.04.1.02]                        │
│                                                                  │
│  STEP 3: Optimize (Rank by Score)                              │
│    Factors (for DSCH move):                                    │
│    • Tier proximity (lower=better, easier to extract)          │
│    • Pile utilization (fuller=better, consolidate stacks)      │
│    • Co-location (with other reefers in pile? yes=better)      │
│                                                                  │
│    Block3.02.1.03: tier=3, util=2/5=40%, same_type=2/2        │
│      score = 0.3*high + 0.4*medium + 0.3*high = 0.78          │
│                                                                  │
│    Block3.02.1.04: tier=4, util=2/5=40%, same_type=2/2        │
│      score = 0.3*low + 0.4*medium + 0.3*high = 0.63           │
│                                                                  │
│    Block3.04.1.02: tier=2, util=1/5=20%, same_type=0/1        │
│      score = 0.3*high + 0.4*low + 0.3*low = 0.50              │
│                                                                  │
│    Ranked: [(Block3.02.1.03, 0.78), (Block3.02.1.04, 0.63),    │
│             (Block3.04.1.02, 0.50)]                            │
│                                                                  │
│  STEP 4: Assign Position                                       │
│    Select top option: Block3.02.1.03                           │
│    Create ContainerYardAssignment:                             │
│      container_visit = Container X                            │
│      position = Block3.02.1.03                                │
│      assigned_by = "Algorithm"                                │
│      algorithm_score = 0.78                                  │
│      reason = "Optimal reefer placement"                      │
│    Container.position = Block3.02.1.03                        │
│                                                                  │
│  OUTPUT: Container assigned to Block3.02.1.03                  │
│          Top 3 alternatives available if override needed       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## API Endpoint Quick Reference

### EDI & Planning (8 endpoints)
| Endpoint | Method | Expected Response | Phase |
|----------|--------|-------------------|-------|
| `/api/edi/upload` | POST | `{ edi_file_id, status }` | 1.1 |
| `/api/transport-visits` | GET/POST | `List[Visit]` or `Visit` | 1.1 |
| `/api/transport-visits/{id}` | PATCH | `Visit` (status updated) | 1.1 |
| `/api/transport-visits/{id}/containers` | GET | `List[ContainerVisit]` with sequence | 1.1 |
| `/api/plan-dsch` | POST | `List[WorkQueue]` | 1.5 |
| `/api/plan-load` | POST | `List[WorkQueue]` (empty, ready for assignment) | 1.5 |
| `/api/planning-preferences` | GET/POST/PATCH | `PlanningPreferences` | 1.5 |
| `/api/planning-status` | GET | `{ visit_id, orders_total, dispatched, completed }` | 1.5 |

### Yard Planning (5 endpoints)
| Endpoint | Method | Expected Response | Phase |
|----------|--------|-------------------|-------|
| `/api/yard-cargo-categories` | GET/POST | `List[YCC]` or `YCC` | 1.2 |
| `/api/yard-position-assignments` | GET/POST | `List[Assignment]` or `Assignment` | 1.2 |
| `/api/container-recommendations` | GET | `List[{ position, score, reasons }]` (top 1-5) | 1.2 |
| `/api/override-assignment` | PATCH | `ContainerYardAssignment` (manual override) | 1.2 |
| `/api/yard-health-check` | GET | `{ all_categorized: bool, uncategorized: List }` | 1.2 |

### Routing (4 endpoints)
| Endpoint | Method | Expected Response | Phase |
|----------|--------|-------------------|-------|
| `/api/pows` | GET/POST | `List[POW]` or `POW` | 1.3 |
| `/api/pow-connections` | GET/POST | `List[POWConnection]` or `POWConnection` | 1.3 |
| `/api/calculate-route` | POST | `{ route: List[POW], distance, time, alternatives }` | 1.3 |
| `/api/order/{id}/calculate-legs` | POST | `List[OrderLeg]` (decomposed order) | 1.3 |

### Dispatching (9 endpoints)
| Endpoint | Method | Expected Response | Phase |
|----------|--------|-------------------|-------|
| `/api/dispatching-policies` | GET/POST/PATCH | `DispatchingPolicy` | 1.4 |
| `/api/che-pools` | GET/POST | `List[ChePool]` or `ChePool` | 1.4 |
| `/api/dispatching-queue` | GET | `List[DispatchingQueue]` (pending) | 1.4 |
| `/api/dispatch-order-leg` | POST | `{ assigned_che, score, alternatives }` | 1.4 |
| `/api/dispatch-batch` | POST | `List[{ order_leg, assigned_che }]` (10 per cycle) | 1.4 |
| `/api/ches/{id}/accept-task` | POST | `{ status: "accepted", task_id }` | 1.4 |
| `/api/ches/{id}/reject-task` | POST | `{ status: "rejected", reason }` | 1.4 |
| `/api/ches/{id}/confirm-pickup` | POST | `{ status: "pickup_confirmed", timestamp }` | 1.4 |
| `/api/ches/{id}/confirm-delivery` | POST | `{ status: "delivery_confirmed", position }` | 1.4 |

### Monitoring (10 endpoints)
| Endpoint | Method | Expected Response | Phase |
|----------|--------|-------------------|-------|
| `/api/ches/{id}/update-status` | POST | `{ ack, che_updated }` | 1.6 |
| `/api/ches/{id}/update-position` | POST | `{ ack, coordinates_updated }` | 1.6 |
| `/api/ches/{id}/log-fault` | POST | `{ ack, fault_id, recovery_est }` | 1.6 |
| `/api/container-visits/{id}/update-status` | POST | `{ ack, status_updated }` | 1.6 |
| `/api/container-visits/{id}/confirm-arrival` | POST | `{ ack, location_confirmed }` | 1.6 |
| `/api/orders/{id}/leg-update` | POST | `{ ack, leg_updated, order_completed? }` | 1.6 |
| `/api/orders/{id}/legs/{leg_id}` | PATCH | `OrderLeg` (status, timestamps) | 1.6 |
| `/api/manual-order-update` | POST | `{ ack, order_created_or_updated }` | 1.6 |
| `/api/batch-status-updates` | POST | `{ processed, failed, errors: List }` | 1.6 |
| `/api/dashboard-metrics` | GET | `{ moves_completed, che_utilization, yard_capacity }` | 1.6 |

---

## Database Schema (Key Tables)

```
LOCATION
  ├─ id, name, location_type
  └─ blocks[], che[], work_queues[], transport_visits[]

TRANSPORT_VISIT
  ├─ id, name, status (Planned→Announced→Arrived→InOperation→Complete→Departed)
  ├─ eta, etd, location_fk
  └─ container_visits[] (many-to-many via TransportVisitContainer)

CONTAINER_VISIT
  ├─ id, container_number, iso_type, requires_power, is_imdg
  ├─ ib_carrier_visit, ob_carrier_visit (vessel references)
  ├─ location_fk, position_fk (current placement)
  ├─ status (Preadvised→Arrived→AtYard→AtQC→OnVessel→Departed→Completed)
  ├─ x_position, y_position (real-time coordinates)
  └─ yard_assignment (ContainerYardAssignment 1:1)

BLOCK
  ├─ id, name, location_fk, block_type
  ├─ max_bays, max_rows, max_tier
  └─ piles[] (auto-generated on save)

PILE
  ├─ id, block_fk, bay, row
  ├─ allows_in, allows_out, allows_20/40/45 (bay-parity rules)
  ├─ x_position, y_position
  └─ positions[] (tiers 1–20)

POSITION
  ├─ id, pile_fk, tier, is_occupied, is_reserved
  └─ container_visit_fk (OneToOne) ← actual container

YARD_CARGO_CATEGORY (YCC)
  ├─ id, name, code, parent_ycc_fk (hierarchy)
  ├─ requires_power, is_imdg, requires_ventilation
  └─ rules (JSON: matching criteria)

YARD_POSITION_ASSIGNMENT
  ├─ id, pile_fk, ycc_fk, priority
  ├─ max_stacking_height, effective_from, effective_to
  └─ (pile assigned to YCC, e.g., Block3_Bay02 → "Reefer 40ft")

CONTAINER_YARD_ASSIGNMENT
  ├─ id, container_visit_fk (1:1), position_fk
  ├─ assigned_by (Manual/Algorithm), algorithm_score
  └─ assigned_at, reason

POINT_OF_WORK (POW)
  ├─ id, name, pow_type (StorageBlock/QC/Transfer/Gate)
  ├─ location_fk, code, x_position, y_position
  └─ capacity, is_active

POW_CONNECTION
  ├─ id, from_pow_fk, to_pow_fk
  ├─ distance, throughput_capacity, allowed_che_types[]
  ├─ is_bidirectional, cost, effective_from, effective_to
  └─ (defines network edges)

CHE
  ├─ id, name, status (Idle/Working/Offline/Breakdown/Maintenance)
  ├─ equipment_type_fk, location_fk
  ├─ is_available, current_block_fk, last_position_fk (POW)
  ├─ x_position, y_position (real-time)
  ├─ fuel_level, last_update_time
  └─ current_tasks[] ← CheCurrentTask[]

CHE_CURRENT_TASK
  ├─ id, che_fk, order_leg_fk
  ├─ status (Assigned/PickupConfirmed/DeliveryConfirmed/Rejected)
  └─ assigned_at, pickup_time, delivery_time

ORDER
  ├─ id, work_queue_fk, container_visit_fk
  ├─ order_type (MOVE_IN/OUT/HOUSEKEEPING/SHIFT)
  ├─ from_position_fk (Pile), to_position_fk (Pile)
  ├─ status (Pending/PartiallyComplete/Complete/Cancelled)
  ├─ etc (expected time to completion), atc (actual)
  └─ order_legs[] (1:many)

ORDER_LEG
  ├─ id, order_fk, che_fk (null initially, assigned by dispatcher)
  ├─ start_pow_fk, end_pow_fk (route segment endpoints)
  ├─ distance, estimated_time
  ├─ status (Pending/Assigned/InProgress/Completed/Failed/Cancelled)
  ├─ pickup_timestamp, delivery_timestamp
  ├─ actual_distance, actual_time, failure_reason
  └─ route (OrderLegRoute pointer)

WORK_QUEUE
  ├─ id, name, location_fk, is_active
  ├─ current_user_fk, description
  └─ orders[] (many)

DISPATCHING_QUEUE
  ├─ id, order_leg_fk, status (Pending/Assigned/Rejected/Cancelled)
  ├─ assigned_che_fk, assigned_at, rejection_reason
  ├─ created_at, priority
  └─ (tracks pending assignment requests)

DISPATCHING_POLICY
  ├─ id, name, location_fk, algorithm (NearestFirst/LeastBusy/BalancedLoad)
  ├─ weight_distance, weight_utilization, weight_fuel
  ├─ max_dispatch_horizon_minutes, is_active
  └─ (configurable dispatch strategy)

CHE_POOL
  ├─ id, name, location_fk
  ├─ equipment_types[] (many-to-many)
  ├─ pows_covered[] (geographies served)
  └─ max_parallel_orders

PRODUCTIVITY_PROFILE
  ├─ id, location_fk, move_type, che_type_fk
  ├─ moves_per_hour, moves_per_container, setup_time_minutes
  └─ (e.g., DSCH + STS → 30 moves/hour)

PLANNING_PREFERENCES
  ├─ id, location_fk
  ├─ dsch_sequence_order, load_sequence_order
  ├─ load_lead_time_minutes, planning_horizon_minutes
  ├─ replan_threshold_variance
  └─ (terminal-specific strategy)
```

---

## Tech Stack Summary

| Layer | Technology | Notes |
|-------|-----------|-------|
| **Backend** | Django 4.2.20 | REST API, ORM, Admin |
| **API Framework** | Django REST Framework 3.16.0 | Serializers, ViewSets |
| **Database** | MySQL 8.0 (mysqlclient 2.2.7) | Production database |
| **Async Tasks** | Celery + Redis | Periodic dispatching every 2 mins |
| **Nested Routing** | drf-nested-routers 0.94.1 | Location → Block → Pile → Position |
| **API Filtering** | django-filter 25.1 | Queryset filtering |
| **API Docs** | drf-spectacular (to add) | OpenAPI/Swagger |
| **MQTT** | paho-mqtt (to add) | SparkPlugB protocol (Phase 3) |
| **Simulator** | Godot 4.2 + GDScript/C# | Equipment & move execution |
| **MES** | Ignition (Phase 3) | Telemetry aggregation |
| **Message Broker** | MQTT Broker (mosquitto) | IoT messaging |

---

## Quick Checklist: What to Review First

- [ ] Read: [FUNCTIONALITY_GAPS_AND_ROADMAP.md](./FUNCTIONALITY_GAPS_AND_ROADMAP.md) (sections 2.1–2.5, then 3)
- [ ] Read: [SPRINT_IMPLEMENTATION_CHECKLIST.md](./SPRINT_IMPLEMENTATION_CHECKLIST.md) (Phase 1, Sprints 1.1–1.4)
- [ ] Review: Existing models in [minitos_core/models.py](./minitos_core/models.py) (lines 1–150)
- [ ] Review: Currently working API endpoints in [minitos_core/urls.py](./minitos_core/urls.py)
- [ ] Understand: Nested router pattern (Location → Block → Pile → Position)
- [ ] Understand: How `Position.validate_physical_rules()` enforces "no gaps" constraint
- [ ] Identify: Which sprints can run in parallel (look at Dependency Matrix in checklist)

---

## Key Success Metrics (EOW4, Phase 1 Complete)

| Metric | Target | Definition |
|--------|--------|-----------|
| **EDI Throughput** | <5 sec for 50 containers | Time from upload to WorkQueues ready |
| **Dispatch Rate** | 100% of pending legs | Orders assigned to CHE (or "NoCHEAvailable") |
| **End-to-End** | <10 sec cycle | EDI → Plan → Dispatch → Execute → Complete |
| **Zero Errors** | 0 unhandled exceptions | All status transitions validated |
| **Documentation** | 100% coverage | API docs + architecture diagrams |

---

**Last Updated:** March 7, 2026  
**Version:** 1.0  
**Contact:** MiniTOS Development Team

