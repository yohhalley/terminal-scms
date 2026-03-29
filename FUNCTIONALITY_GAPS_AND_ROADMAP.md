# MiniTOS MVP - Functionality Gaps & Implementation Roadmap

**Date:** March 2026  
**Status:** Analysis Phase  
**Scope:** EDI Processing → Planning → Dispatching → Equipment Management

---

## 1. CURRENT STATE ANALYSIS

### 1.1 What Exists (✅ Implemented)

#### **Spatial/Physical Model (Tier 1: Foundation)**
- **Location** - Terminal types (Terminal, Inland Depot, Vessel, Truck, Train)
- **Block** - Storage zones with auto-generated pile grid (bay × row × tier)
- **Pile** - Individual stacks with size restrictions (20/40/45 ft by bay parity)
- **Position** - Specific tier slots with container assignment validation
- **Container Hierarchy** - ISOType, ContainerUnit, ContainerVisit, ContainerOwner

#### **Equipment & Tasks (Tier 2: Partial)**
- **Che** - Equipment entities with status (Idle/Working/Offline/Breakdown/Maintenance), location, fuel, maintenance tracking
- **EquipmentType** - Equipment taxonomy (TT, SC, RTG, ASC, STS)
- **WorkQueue** - Basic task container (name, user, location)
- **Order** - Move orders with type (MOVE_IN, MOVE_OUT, HOUSEKEEPING, SHIFT)
- **OrderLeg** - Order decomposition into equipment movements

#### **Reference Data (Tier 3: Basic)**
- **Route** - Vessel route profile (move counts, time windows)
- **Visit** - Voyage planning entity
- **POD** - Port of discharge/destination

#### **API Structure (Tier 4: Partially Complete)**
- Nested routers for Location → Block → Pile → Position hierarchy
- Basic CRUD endpoints for models
- ViewSet pattern with queryset filtering
- Partial updates (PATCH) support

---

### 1.2 What's Missing (❌ Critical Gaps)

| **Category** | **Component** | **Impact** | **Complexity** |
|---|---|---|---|
| **Visit Lifecycle** | Transport/Vessel Visit distinction | Cannot process EDI or sequence moves | HIGH |
| **Visit Lifecycle** | Visit Status state machine | No operational tracking (Announced → Arrived → In Operation → Complete) | MEDIUM |
| **Visit Lifecycle** | EDI Processing pipeline | Manual container entry only; no bulk EDI import | CRITICAL |
| **Yard Planning** | Yard Cargo Category (YCC) hierarchy | No intelligent container assignment | HIGH |
| **Yard Planning** | Yard Position Category Assignment | Containers placed randomly without logic | HIGH |
| **Yard Planning** | Planning algorithm (search & optimization) | No container location selection | HIGH |
| **Routing System** | Point of Work (POW) model | No path-finding infrastructure | HIGH |
| **Routing System** | POW connections (graph model) | No distance/capacity/equipment routing | HIGH |
| **Routing System** | Route calculation engine | Manual route creation; no automation | HIGH |
| **Dispatching** | Dispatching algorithm | No CHE assignment logic | CRITICAL |
| **Dispatching** | CHE availability/pool model | Equipment assigned ad-hoc | MEDIUM |
| **Monitoring** | Update APIs from simulator | No real-time status sync | CRITICAL |
| **Monitoring** | Equipment status update endpoint | CHE status stuck on creation | MEDIUM |
| **Monitoring** | Order/Container status updates | No move execution tracking | MEDIUM |
| **Operations** | Time estimation (productivity model) | No move duration calculation | MEDIUM |
| **Operations** | Rehandling logic | No container re-planning support | LOW (Phase 2) |
| **Operations** | Restow handling | Out of scope (Phase 1) | LOW (Phase 2) |
| **Integration** | MQTT message handling | Messages from Godot → TOS not processed | CRITICAL |
| **Integration** | SparkPlugB telemetry ingestion | Equipment telemetry not received | MEDIUM |

---

## 2. FUNCTIONAL GAPS BREAKDOWN BY FEATURE

### 2.1 EDI Processing App (SIMPLIFIED APPROACH)

**Current State:** No EDI processing; manual `ContainerVisit` creation only.

**Gap:** Cannot bulk-import container manifests from shipping lines.

**Updated Approach (Sprint 1.1):** Skip complex EDI parsing. Instead, provide bulk container creation API that accepts container data directly. EDI processing will be handled by external systems that call our bulk API.

#### **Models Updated (Sprint 1.1):**
```
ContainerVisit (updated)
  - bill_of_lading: CharField (NEW - links to shipping manifest)
  - booking: CharField (NEW - booking reference)
  - status: [Preadvised, Planned, InYard, Loaded, Discharged, Departed]  ← ENHANCED STATE MACHINE
  - ib_carrier: FK(Visit) (inbound carrier)
  - ob_carrier: FK(Visit) (outbound carrier)

Visit (updated)
  - visit_type: [Vessel, Barge, Truck, Train] (NEW)
  - status: [Planned, Announced, Arrived, InOperation, Complete, Departed]  ← ENHANCED STATE MACHINE
  - vessel_name: CharField (for vessel visits)
  - eta: DateTimeField
  - etd: DateTimeField

Route (simplified)
  - name: CharField
  - window_start: DateTimeField
  - window_end: DateTimeField
  - periodicity: [Daily, Weekly, Biweekly, Monthly] (NEW)
```

#### **APIs Added (Sprint 1.1):**
```
POST /api/bulk-create-container-visits
  - Input: { "containers": [{container_number, iso_type, bill_of_lading, booking, ib_carrier_id, ob_carrier_id}, ...] }
  - Output: { success: true, created_visits: [id1, id2, ...] }
  - Process: Bulk create ContainerVisit records with validation

POST /api/visits/{visit_id}/transition-status
  - Input: { "status": "Arrived" }
  - Output: Updated visit data
  - Process: State machine transition with validation

POST /api/container-visits/{container_id}/transition-status
  - Input: { "status": "InYard" }
  - Output: Updated container visit data
  - Process: Container status transition

GET /api/visits/{visit_id}/status-summary
  - Output: { discharge_containers: X, load_containers: Y, total: Z }
  - Process: Count containers linked to visit via ib_carrier/ob_carrier
```

#### **Services/Logic Added (Sprint 1.1):**
```
class VisitStateManager:
  - get_allowed_transitions(current_status) → List[Status]
  - validate_transition(from_status, to_status) → bool
  - transition_visit_status(visit, new_status, user) → (success, message)
  - transition_container_status(container_visit, new_status, user) → (success, message)

BulkContainerVisitSerializer:
  - validate_container_data(data) → validated data
  - create_container_visits(validated_data) → List[ContainerVisit]
```

---

### 2.2 Yard Planning System

**Current State:** No bay/location assignment logic; `Position` assigned randomly or not at all.

**Gap:** Containers placed into yard without considering cargo type, vessel BOL, or optimization rules.

#### **Models to Add:**
```
YardCargoCategory (YCC - new)
  - name: CharField (e.g., "Reefer Import", "OOG Break Bulk", "Standard Export")
  - code: CharField (unique)
  - description: TextField
  - requires_power: BooleanField
  - is_imdg: BooleanField
  - requires_ventilation: BooleanField
  - parent_category: FK(YardCargoCategory, null=True)  ← hierarchy (General → Reefer → Reefer 20ft)
  ├── defines container characteristics

YardPositionAssignment (new)
  - pile: FK(Pile)
  - yard_cargo_category: FK(YardCargoCategory)
  - priority: IntegerField (1=preferred, 10=fallback)
  - max_stacking_height: IntegerField (tiers allowed)
  - effective_from: DateTimeField
  - effective_to: DateTimeField (null = ongoing)
  ├── links physical locations to cargo types with temporal validity

ContainerYardAssignment (new)
  - container_visit: OneToOne(ContainerVisit)
  - position: FK(Position)
  - assigned_by: [Manual, Algorithm, Replanning]
  - algorithm_score: FloatField (if auto-assigned)
  - assigned_at: DateTimeField
  - reason: TextField
  ├── audit trail for placement decisions

ContainerClassification (new)
  - container_visit: FK(ContainerVisit)
  - yard_cargo_category: FK(YardCargoCategory)
  - confidence: FloatField (0-1)
  - matched_rules: JSONField
  ├── tracks which YCC rules matched
```

#### **APIs to Add:**
```
GET /api/yard-cargo-categories/
  - List all YCC with hierarchy

POST /api/yard-cargo-categories/
  - Create new YCC

GET /api/locations/{location_pk}/yard-position-assignments/
  - List pile → YCC mappings

POST /api/locations/{location_pk}/yard-position-assignments/
  - Assign cargo category to piles

POST /api/locations/{location_pk}/container_visits/{visit_pk}/assign-yard-position
  - Input: container_visit_id, (optional) preferred_position
  - Output: ContainerYardAssignment with recommended position(s)
  - Logic: Apply search + optimization algorithm

GET /api/locations/{location_pk}/container_visits/{visit_pk}/position-recommendations
  - Input: container_visit_id, limit (top 1 or top N)
  - Output: List[{position, score, reasons}]
  - Supports "give me top 5 options"

PATCH /api/locations/{location_pk}/container_visits/{visit_pk}/override-assignment
  - Manual override of system-assigned position

GET /api/yard/health-check
  - Validate: All yard containers match at least 1 YCC ✓
```

#### **Algorithms to Implement:**

##### **A. Container Classification (Categorization)**
```
Algorithm: Classify_Container_To_YCC(container_visit) → List[(YCC, confidence)]
  1. Extract container attributes:
     - is_reefer = container_visit.requires_power
     - is_imdg = container_visit.is_imdg
     - iso_type = container_visit.container_number.iso_type
     - pod = container_visit.pod
     - category = container_visit.category (Import/Export/Transshipment)
  
  2. Initialize YCC rule engine:
     - Load YCCs with rules (JSON rules field)
       Example rule: { requires_power: true, is_imdg: false, iso_type: [20, 40] }
  
  3. Match container to YCC hierarchy:
     - Bottom-up: Check leaf YCCs first (more specific)
     - If match found, stop (use most specific)
     - Otherwise, backtrack to parent categories
  
  4. Return: List[(YCC, match_confidence, matched_rule_keys)]
  
  5. Edge case: Container doesn't match any YCC
     - Fallback to "Uncategorized" YCC (create if missing)
     - Log warning: "Container X not in any YCC"
```

##### **B. Position Search (Find Available Positions)**
```
Algorithm: Search_Positions_For_YCC(ycc, location, filters={}) → List[Position]
  1. Get YCC's assigned piles:
     positions_assignments = YardPositionAssignment.objects.filter(
       yard_cargo_category=ycc, 
       effective_from <= now <= effective_to
     )
     eligible_piles = [pa.pile for pa in positions_assignments]
  
  2. Filter piles by constraints:
     - Can pile accept container? pile.can_accept_container(container)
     - Is pile.allows_in = True?
     - Respect pile.max_stacking_height
  
  3. Per eligible pile, find available positions:
     - For each position in pile.positions (ordered by tier):
       - If position.is_occupied = False AND position.is_reserved = False:
         - Validate "no gaps" rule: all tiers below are occupied
         - Add to available_positions[]
  
  4. Return: available_positions (sorted by tier, then by pile priority)
  
  5. Edge case: No positions available
     - Return empty list (triggers "no space" warning)
```

##### **C. Position Optimization (Rank Positions)**
```
Algorithm: Optimize_Positions(container_visit, available_positions, move_type) → List[(Position, score)]
  1. Scoring factors (configurable weights):
     - Proximity to vessel QC (if LOAD move)
       score += weight_proximity × (distance_to_qc / max_distance)
     - Pile utilization (prefer fuller piles to reduce future moves)
       score += weight_utilization × (occupied_tiers / max_tier)
     - Stacking efficiency (prefer lower tiers to minimize future extracting)
       score += weight_tier × (max_tier - position.tier) / max_tier
     - Cargo co-location (if container is reefer, co-locate with other reefers)
       score += weight_colocation × (similar_containers_in_pile / pile_capacity)
     - Access frequency (if container POD is main port, place for easy extraction)
       score += weight_access × (pod_frequency_rank / max_pods)
  
  2. Move-type-specific tuning:
     - DSCH (discharge from vessel):
       - Move to easily accessible positions (lower tiers)
       - Prioritize stacking (fill piles quickly)
     - LOAD (load to vessel):
       - Place near QC (reduce CHE travel)
       - Prioritize quick access (higher priority for tier 1)
     - YARD (internal move):
       - Balance utilization and access
  
  3. Normalize scores to 0-1 range
  
  4. Return: Sorted List[(Position, final_score)] descending by score
  
  Example output:
    [{position: Block1.01.01.01, score: 0.92, components: {proximity: 0.8, utilization: 0.88, ...}},
     {position: Block1.01.02.01, score: 0.85, components: {...}},
     ...]
```

#### **Services/Logic to Build:**
```
class YardPlanningService:
  - classify_container(container_visit) → YardCargoCategory
  - search_available_positions(ycc, location) → List[Position]
  - optimize_position(container_visit, positions, move_type) → List[(Position, score)]
  - assign_position(container_visit, position, reason) → ContainerYardAssignment
  - validate_yard_health() → {all_categorized: bool, uncategorized_containers: List}
  - replan_container(container_visit, new_optimization_rules) → ContainerYardAssignment

class YCCHierarchyManager:
  - get_hierarchy_tree() → Dict (nested structure)
  - find_matching_ycc(container_attributes) → List[YCC] (ordered by specificity)
  - get_fallback_ycc() → YCC (safe default)
```

---

### 2.3 Routing System

**Current State:** No POW model; no path-finding; routes created manually.

**Gap:** Cannot auto-calculate container movement paths through terminal (Block 1 → Staging → QC1).

#### **Models to Add:**
```
PointOfWork (POW - new)
  - name: CharField (e.g., "Block1", "QC1", "Block1_TP", "QC1_TP", "Truck Gate")
  - pow_type: [StorageBlock, QC, GateEntry, StagingArea, Transfer]
  - location: FK(Location)
  - code: CharField (unique per location, e.g., "BLK1", "QC01")
  - x_position, y_position: Float (coordinates)
  - capacity: IntegerField (max containers)
  - is_active: BooleanField
  ├── models all important terminal touchpoints

POWConnection (new)
  - from_pow: FK(PointOfWork)
  - to_pow: FK(PointOfWork)
  - distance: IntegerField (meters)
  - throughput_capacity: IntegerField (containers/hour)
  - allowed_che_types: Many2Many(EquipmentType)
  - is_bidirectional: BooleanField
  - cost: FloatField (for path-finding heuristic)
  - effective_from, effective_to: DateTimeField
  ├── defines terminal network topology

OrderLegRoute (new)
  - order_leg: FK(OrderLeg)
  - sequence: IntegerField  (1st leg, 2nd leg, etc.)
  - pows: Many2Many(PointOfWork) with through_fields
     Example: Block1 → Block1_TP → QC1_TP → QC1
  - estimated_travel_time: IntegerField (seconds)
  - estimated_distance: IntegerField (meters)
  - created_at: DateTimeField
  ├── explicit route for each leg
```

#### **APIs to Add:**
```
GET /api/locations/{location_pk}/pows/
  - List all POWs in location

POST /api/locations/{location_pk}/pows/
  - Create new POW

GET /api/locations/{location_pk}/pow-connections/
  - List all POW connections

POST /api/locations/{location_pk}/pow-connections/
  - Create connection between two POWs

POST /api/locations/{location_pk}/calculate-route
  - Input: { from_pow_id, to_pow_id, che_type }
  - Output: { route: List[POW], distance, travel_time, alternative_routes? }
  - Uses A* or Dijkstra pathfinding

POST /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/calculate-legs
  - Input: Order with from_position (Pile) and to_position (Pile)
  - Output: List[OrderLeg] with POW routes calculated
  - Logic: 
    1. from_pile → nearest POW (e.g., Block1)
    2. POW network → destination POW (e.g., QC1)
    3. destination POW → to_pile
```

#### **Algorithms to Implement:**

##### **A. POW Mapping (Pile ↔ POW)**
```
Algorithm: Get_POW_For_Pile(pile, location) → POW
  1. Find block's corresponding POW:
     - Query: POW.objects.filter(location=location, code=pile.block.name)
     - Example: Block1 → POW(code="BLK1")
  2. If not found, create it if auto_create flag set
  3. Return: POW
  
Algorithm: Get_POW_For_Location(location_type, location_name) → POW
  1. Map location type to POW:
     - LocationType="VL" (Vessel) → POW(pow_type="QC")
     - LocationType="TK" (Truck) → POW(pow_type="GateEntry")
  2. Return: POW
```

##### **B. Path-Finding Algorithm (Dijkstra/A*)**
```
Algorithm: Find_Shortest_Path(from_pow, to_pow, che_type, location) → List[POW]
  1. Build graph:
     - Nodes = all active POWs in location
     - Edges = POWConnections (filter by che_type if needed)
     - Edge costs = distance or time-based
  
  2. Apply Dijkstra/A* algorithm:
     - Start: from_pow
     - Goal: to_pow
     - Heuristic (A*): Euclidean distance to goal
  
  3. Constraints:
     - Only use connections with allowed_che_types including che_type
     - Skip inactive POWs
     - Skip connections outside effective_from/to date range
  
  4. Return: List[POW] representing shortest path
  
  5. Edge case: No path exists
     - Log error
     - Return empty list (trigger "no route" error)

Algorithm: Find_Alternative_Routes(from_pow, to_pow, che_type, location, limit=3) → List[List[POW]]
  1. Run Dijkstra/A* (get best path)
  2. Remove edges from best path's connections
  3. Run Dijkstra/A* again (get 2nd best)
  4. Repeat until limit reached
  5. Return: List of alternative routes (ranked by cost)
```

##### **C. Order Leg Decomposition**
```
Algorithm: Decompose_Order_Into_Legs(order) → List[OrderLeg]
  Input: Order { from_position: Position, to_position: Position, che_type }
  
  1. Map positions to POWs:
     from_pow = Get_POW_For_Pile(order.from_position.pile)
     to_pow = Get_POW_For_Pile(order.to_position.pile)
  
  2. Calculate route:
     route = Find_Shortest_Path(from_pow, to_pow, order.che_type)
     Example route: [Block1, Block1_TP, QC1_TP, QC1]
  
  3. Segment route into logical legs:
     - Define "leg boundary" as transitions between different blocks/zones
     - Example legs:
       Leg 1: Block1 → Block1_TP (yard move, RTG/ASC)
       Leg 2: Block1_TP → QC1_TP (transfer move, TT/SC)
       Leg 3: QC1_TP → QC1 (quay move, STS/QC)
  
  4. Create OrderLeg for each segment:
     For each leg:
       - che = null (not yet assigned by dispatcher)
       - start_pow = pows[i]
       - end_pow = pows[i+1]
       - distance = Calculate_leg_distance(leg)
       - estimated_time = distance / expected_che_speed
  
  5. Return: List[OrderLeg] ordered by sequence

Algorithm: Calculate_Leg_Distance(leg) → Int (meters)
  1. Sum all POWConnection distances in leg
  2. Add "staging delays" (time at intermediate POWs)
  3. Return: total distance
```

#### **Services/Logic to Build:**
```
class RoutingService:
  - find_shortest_path(from_pow, to_pow, che_type) → List[POW]
  - find_alternative_routes(from_pow, to_pow, che_type, limit) → List[List[POW]]
  - decompose_order_into_legs(order) → List[OrderLeg]
  - calculate_leg_distance(leg) → Int
  - create_pow_from_block(block) → POW (auto-creation helper)
  - validate_pow_network() → {is_connected: bool, isolated_pows: List}

class POWNetworkManager:
  - build_network_graph(location) → Graph
  - get_network_topology(location) → Dict (visualization data)
  - validate_connection(from_pow, to_pow) → (is_valid, reason)
```

---

### 2.4 Dispatching System

**Current State:** No assignment logic; `OrderLeg.che` must be manually set.

**Gap:** When an order is ready, no automatic CHE assignment or smart allocation.

#### **Models to Add:**
```
DispatchingPolicy (new)
  - name: CharField (e.g., "Nearest First", "Least Busy", "Balanced Load")
  - location: FK(Location)
  - algorithm: CharField (choices: [NearestFirst, LeastBusy, BalancedLoad, Custom])
  - weight_distance: FloatField (0-1, higher = prioritize closeness)
  - weight_utilization: FloatField (0-1, higher = balance workload)
  - weight_fuel: FloatField (0-1, higher = conserve fuel)
  - max_dispatch_horizon_minutes: IntegerField (e.g., 120 = max 2-hour lookahead)
  - is_active: BooleanField
  ├── configurable dispatching strategy

ChePool (new)
  - name: CharField (e.g., "RTG Pool A", "Quay Cranes")
  - location: FK(Location)
  - equipment_types: Many2Many(EquipmentType)
  - pows_covered: Many2Many(PointOfWork)
  - max_parallel_orders: IntegerField (1 for STS, 2+ for RTGs)
  ├── logical grouping of equipment sharing geography/types

DispatchingQueue (new)
  - order_leg: FK(OrderLeg)
  - created_at: DateTimeField
  - status: [Pending, Assigned, Rejected, Cancelled]
  - assigned_che: FK(Che, null=True)
  - assigned_at: DateTimeField(null=True)
  - rejection_reason: TextField (if status=Rejected)
  - priority: IntegerField (1=urgent, 10=low, for sorting)
  ├── tracks pending assignment requests

CheCurrentTask (new)
  - che: FK(Che)
  - order_leg: FK(OrderLeg)
  - status: [Assigned, PickupConfirmed, DeliveryConfirmed, Rejected]
  - assigned_at: DateTimeField
  - pickup_time: DateTimeField(null=True)
  - delivery_time: DateTimeField(null=True)
  ├── tracks active task per CHE
```

#### **APIs to Add:**
```
GET /api/dispatching-policies/
  - List dispatch strategies

POST /api/dispatching-policies/
  - Create new strategy

GET /api/locations/{location_pk}/che-pools/
  - List equipment pools

POST /api/locations/{location_pk}/che-pools/
  - Create pool

GET /api/locations/{location_pk}/dispatching-queue
  - List pending order legs (unassigned)
  - Query params: limit=10, priority_filter="urgent"

POST /api/locations/{location_pk}/dispatch-order-leg
  - Input: { order_leg_id, (optional) preferred_che_id }
  - Output: { assigned_che, alternatives?, status }
  - Triggers dispatching algorithm

POST /api/locations/{location_pk}/dispatch-batch
  - Input: { order_leg_ids: List, policy_id }
  - Output: { assignments: List[(order_leg_id, assigned_che)], details }
  - Bulk dispatch for multiple legs (e.g., every 2 minutes)

POST /api/locations/{location_pk}/ches/{che_pk}/accept-task
  - Input: { order_leg_id, confirm_pickup_time }
  - Output: { status: "accepted", ack }
  - Called from Godot/equipment when accepting dispatch

POST /api/locations/{location_pk}/ches/{che_pk}/reject-task
  - Input: { order_leg_id, reason }
  - Output: { status: "rejected"  }
  - Called from Godot/equipment when refusing dispatch (busy, fault, etc.)

POST /api/locations/{location_pk}/ches/{che_pk}/confirm-pickup
  - Input: { order_leg_id, pickup_time }
  - Output: { status: "pickup_confirmed" }
  - Called from equipment when container picked up

POST /api/locations/{location_pk}/ches/{che_pk}/confirm-delivery
  - Input: { order_leg_id, delivery_time, final_position }
  - Output: { status: "delivery_confirmed", container_visit_updated }
  - Called from equipment when container delivered
```

#### **Algorithms to Implement:**

##### **A. CHE Availability Check**
```
Algorithm: Get_Available_CHE_For_Leg(order_leg) → List[Che]
  1. Filter by geography:
     - che_can_reach_start = CHE.objects.filter(
         current_pow_id in order_leg.start_pow connections
       )
  
  2. Filter by equipment type:
     - che_matches_type = che_can_reach_start.filter(
         equipment_type in order_leg.allowed_che_types
       )
  
  3. Filter by availability:
     - available_che = che_matches_type.filter(
         status in [Idle, Available],
         is_available=True
       ).exclude(
         che_current_tasks.filter(status != [Completed, Cancelled])
       )  ← no active tasks
  
  4. Filter by capacity:
     - che_with_capacity = [che for che in available_che 
                             if che.current_task_count < che.max_parallel_orders]
  
  5. Return: che_with_capacity
  
  7. Edge case: No CHE available
     - Return empty list (status: "NoCHEAvailable")
```

##### **B. CHE Selection (Scoring)**
```
Algorithm: Score_CHE_For_Leg(che, order_leg, policy) → Float
  Input:
    - che: Equipment candidate
    - order_leg: Leg needing assignment
    - policy: DispatchingPolicy with weights
  
  1. Calculate distance score:
     - current_to_start_distance = Calculate_Distance(che.current_position, order_leg.start_position)
     - distance_score = 1 - (current_to_start_distance / max_distance)  ← 0-1, higher=closer
  
  2. Calculate utilization score (balance workload):
     - che_current_load = len(che.current_tasks)
     - pool_avg_load = average(len(che.current_tasks) for che in same_pool)
     - utilization_delta = (pool_avg_load - che_current_load) / pool_avg_load
     - utilization_score = 0.5 + (utilization_delta * 0.5)  ← 0-1, higher=less busy
  
  3. Calculate fuel efficiency score:
     - fuel_score = che.fuel_level / 100  ← prefer higher fuel
  
  4. Combine scores:
     final_score = (
       policy.weight_distance * distance_score +
       policy.weight_utilization * utilization_score +
       policy.weight_fuel * fuel_score
     )
     ← normalized to 0-1
  
  5. Return: final_score

Algorithm: Select_Best_CHE(available_che, order_leg, policy) → Che
  1. Score each CHE:
     scores = [(che, Score_CHE_For_Leg(che, order_leg, policy)) 
               for che in available_che]
  
  2. Sort by score descending
  
  3. Return: scores[0][0]  ← top-ranked CHE
  
  Note: Caller may use scores[0:5] for "alternatives"
```

##### **C. Batch Dispatching (Periodic Worker)**
```
Algorithm: Periodic_Dispatch_Worker (runs every 2 minutes):
  1. Get pending order legs:
     pending = DispatchingQueue.objects.filter(
       status=Pending,
       created_at <= now - DISPATCH_MIN_AGE
     ).order_by('priority', 'created_at')[:BATCH_SIZE]
  
  2. Get active dispatching policy for location:
     policy = DispatchingPolicy.objects.filter(
       location=location, is_active=True
     ).first()
  
  3. For each pending order_leg:
     available_che = Get_Available_CHE_For_Leg(order_leg)
     if not available_che:
       - Mark DispatchingQueue: status=Rejected, reason="NoCHEAvailable"
       - Trigger alert: "No CHE available for order X"
       - Continue to next order_leg
     else:
       best_che = Select_Best_CHE(available_che, order_leg, policy)
       - Assign: order_leg.che = best_che
       - Mark DispatchingQueue: status=Assigned, assigned_che=best_che
       - Create CheCurrentTask: status=Assigned
       - Send to CHE: MQTT dispatch message or API POST
  
  4. Update CHE status:
     - If CHE has existing task: keep status=Working
     - If CHE newly assigned task: status=Working
     - If CHE idle & no more tasks: status=Idle
```

#### **Services/Logic to Build:**
```
class DispatchingService:
  - get_available_che_for_leg(order_leg) → List[Che]
  - score_che_for_leg(che, order_leg, policy) → Float
  - select_best_che(available_che, order_leg, policy) → Che
  - dispatch_order_leg(order_leg, preferred_che=None, policy=None) → (assigned_che, score)
  - dispatch_batch(order_leg_ids, policy) → List[(order_leg_id, assigned_che, score)]
  - reject_dispatch(order_leg, reason) → None
  - batch_dispatch_worker(location, time_now) → Dict (stats)

class CheTaskManager:
  - assign_task_to_che(che, order_leg) → CheCurrentTask
  - accept_task(che, order_leg) → CheCurrentTask (status=Accepted)
  - reject_task(che, order_leg, reason) → DispatchingQueue (mark rejected)
  - complete_task(che, order_leg) → CheCurrentTask (status=Completed)
  - get_che_current_load(che) → Int (number of active tasks)
  - get_pool_average_load(pool) → Float
```

---

### 2.5 Monitoring & Update APIs

**Current State:** No endpoints to receive simulator updates; CHE/Order status not updated in real-time.

**Gap:** Simulator/equipment cannot notify TOS of status changes, completions, faults.

#### **Models to Augment:**
```
ContainerVisit (extend)
  - status: [Preadvised, Arrived, AtYard, AtQC, OnVessel, Departed, Completed]  ← add status tracking
  - position: FK(Position)  ← current physical position
  - last_updated: DateTimeField

Che (extend)
  - status: [Idle, Working, Offline, Breakdown, Maintenance]  ← already exists, ensure used
  - is_available: BooleanField  ← already exists
  - last_position: FK(POW)  ← track CHE location updates
  - last_update_time: DateTimeField
  - total_fuel_consumed: FloatField
  - total_moves_completed: IntegerField

OrderLeg (extend)
  - status: [Pending, Assigned, InProgress, Completed, Failed, Cancelled]
  - pickup_timestamp: DateTimeField(null=True)
  - delivery_timestamp: DateTimeField(null=True)
  - actual_distance: IntegerField(null=True)  ← vs estimated
  - actual_time: IntegerField(null=True)  ← vs estimated
  - failure_reason: TextField(null=True)

Order (extend)
  - status: [Pending, PartiallyComplete, Complete, Cancelled]
```

#### **APIs to Add:**

```
# Equipment Status Updates
POST /api/locations/{location_pk}/ches/{che_pk}/update-status
  - Input: { status, position, fuel_level, is_available }
  - Output: { ack }
  - Idempotent: if same status, no-op

POST /api/locations/{location_pk}/ches/{che_pk}/update-position
  - Input: { x, y, z, pow_id }
  - Output: { ack }
  - Update CHE real-time location

POST /api/locations/{location_pk}/ches/{che_pk}/log-fault
  - Input: { fault_code, description, severity }
  - Output: { ack, estimated_resolution_time }
  - Log equipment fault/breakdown

# Container Status Updates
POST /api/locations/{location_pk}/container_visits/{visit_pk}/update-status
  - Input: { status, position_id }
  - Output: { ack, container_visit_updated }

POST /api/locations/{location_pk}/container_visits/{visit_pk}/confirm-arrival
  - Input: { arrival_time, confirmed_position }
  - Output: { ack, visit_status_updated }
  - Container physically arrived at terminal

# Order & OrderLeg Status Updates
POST /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/leg-update
  - Input: { order_leg_id, status, pickup_time?, delivery_time?, actual_position? }
  - Output: { ack, order_leg_updated, order_status_if_complete }

PATCH /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/order_legs/{leg_pk}
  - Input: { status, timestamps, failure_reason }
  - Output: updated OrderLeg

POST /api/locations/{location_pk}/manual-order-update
  - Input: { container_visit_id, from_position, to_position, status, reason }
  - Output: { ack, order_created_if_missing, order_leg_updated }
  - Handle move executed WITHOUT prior TOS order

# Bulk Status Updates (for efficiency)
POST /api/batch-status-updates
  - Input: { updates: [{ type, id, status, data }] }
  - Output: { processed, failed, errors }
  - Batch process multiple updates in one request
```

#### **Services/Logic to Build:**
```
class EventProcessingService:
  - update_che_status(che_id, status_data) → Che
  - update_container_visit_status(container_visit_id, status_data) → ContainerVisit
  - update_order_leg_status(order_leg_id, status_data) → OrderLeg
  - process_order_completion(order_leg_id) → Check if all legs complete
  - aggregate_che_stats(che_id, period) → Dict (moves, fuel, efficiency)

class ManualOrderHandling:
  - detect_manual_move(container_visit_id, from_pos, to_pos) → bool
  - create_retroactive_order(container_visit_id, from_pos, to_pos, move_type) → Order
  - log_move_discrepancy(container_visit_id, reason) → LogEntry
```

---

### 2.6 Planning App Logic

**Current State:** No WorkQueue/Order sequencing; no time estimation.

**Gap:** Cannot auto-create orders from vessel manifest; no productivity modeling.

#### **Models to Add:**
```
ProductivityProfile (new)
  - location: FK(Location)
  - move_type: [DSCH, LOAD, Yard]
  - che_type: FK(EquipmentType)
  - moves_per_hour: IntegerField (e.g., 30 for STS)  - alternate: seconds_per_move
  - moves_per_container: IntegerField (1 for single, 2+ for doubles)
  - setup_time_minutes: IntegerField (time between moves)
  ├── models productivity of different equipment/move types

PlanningPreferences (new)
  - location: FK(Location)
  - dsch_sequence_order: [PortToSeaSide, SeaSideToPort, Custom]
  - load_sequence_order: [Custom, SeaSideToPort]
  - load_lead_time_minutes: IntegerField (e.g., 10 = containers ready 10 mins before op)
  - planning_horizon_minutes: IntegerField (e.g., 120 = plan 2 hours ahead)
  - replan_threshold_variance: Float (e.g., 0.1 = replan if 10% delay)
  ├── strategy config for planning

WorkOrderSequence (new) - maybe use Order.etc to set sequence timing
  - order: FK(Order)
  - sequence_number: IntegerField (1, 2, 3, ... in order)
  - planned_start_time: DateTimeField
  - planned_end_time: DateTimeField
  - estimated_duration_seconds: IntegerField
  ├── explicit timing for each order
```

#### **APIs to Add:**
```
POST /api/locations/{location_pk}/transport_visits/{visit_pk}/plan-dsch
  - Input: { wq_config: { sequence_order, bay_grouping } }
  - Output: List[WorkQueue] with Orders sequenced
  - Logic: 
    1. Get all DSCH containers from visit
    2. Group by bay (if bay_grouping=true)
    3. Create WorkQueue per bay
    4. For each container, create Order with sequence
    5. Apply time estimations
    6. Trigger Yard Planning for each container

POST /api/locations/{location_pk}/transport_visits/{visit_pk}/plan-load
  - Input: { wq_config: { num_work_queues, containers_per_wq } }
  - Output: List[WorkQueue] (ready for user to assign containers)

POST /api/locations/{location_pk}/planning-preferences
  - CRUD for planning strategy

GET /api/locations/{location_pk}/planning-status
  - Output: { visit_id, orders_total, orders_planned, orders_dispatched, orders_completed }
```

#### **Services/Logic to Build:**
```
class PlanningService:
  - create_workqueues_for_dsch(visit, num_queues=None) → List[WorkQueue]
  - create_workqueues_for_load(visit, num_queues) → List[WorkQueue]
  - sequence_orders_by_time(orders, base_time, productivity_profile) → None (sets Order.etc)
  - estimate_order_duration(order, productivity_profile) → Int (seconds)
  - trigger_yard_planning_for_horizon(location, horizon_minutes) → Dict (assignments_count)
  - replan_delayed_orders(location, threshold=10% variance) → Dict (replanned_count)

class TimeEstimationService:
  - get_productivity_profile(location, move_type, che_type) → ProductivityProfile
  - estimate_leg_time(order_leg, che) → Int (seconds)
  - calculate_dsch_sequence_times(visit) → Dict {order_id: start_time}
  - calculate_load_sequence_times(visit, lead_time_minutes) → Dict {order_id: start_time}
```

---

### 2.7 Integration Points (API Updates)

#### **Simulator Updates Needed:**

1. **Subscribe to Dispatching Events:**
   - MQTT topic: `/minitos/{location}/dispatching/order-assigned`
   - Message: `{ order_leg_id, assigned_che_id, route, estimated_time }`

2. **Report Task Completion:**
   - MQTT/API: `POST /api/order-leg-complete`
   - Includes: pickup_time, delivery_time, actual_distance

3. **Report Equipment Events:**
   - Status changes: `POST /api/ches/{id}/status-update`
   - Position updates: `POST /api/ches/{id}/position-update`
   - Faults: `POST /api/ches/{id}/fault-log`

---

## 3. IMPLEMENTATION ROADMAP

### **Phase 1: Foundation (Weeks 1-4)**
*Goal: Basic data model and APIs for EDI → Planning → Dispatching flow*

#### **Sprint 1.1: EDI & Visit Lifecycle (Week 1)**
- [ ] Create EDIFile, EDIContainer, TransportVisit, VesselVisit, TransportVisitContainer models
- [ ] Add Visit Status state machine (Planned → Announced → Arrived → InOperation → Complete → Departed)
- [ ] Implement EDIProcessor service (parse, validate, duplicate check)
- [ ] Add API: `POST /api/edi/upload`, `POST /api/transport-visits`, `PATCH /api/transport-visits/{id}` 
- [ ] **Test:** EDI file upload → creates TransportVisit + 100 ContainerVisits

#### **Sprint 1.2: Yard Cargo Categories & Assignment (Week 1.5)**
- [ ] Create YardCargoCategory, YardPositionAssignment, ContainerClassification, ContainerYardAssignment models
- [ ] Implement YCCHierarchyManager (build hierarchy)
- [ ] Implement YardPlanningService (classify, search, optimize)
- [ ] Add 3 optimization algorithms (see 2.2.C above)
- [ ] Add API: `POST /api/yard-cargo-categories/`, `GET /api/container-recommendations`, `PATCH /api/override-assignment`
- [ ] **Test:** Container auto-assigned to 3-tier YCC hierarchy; verify no gaps

#### **Sprint 1.3: Routing System (Week 2)**
- [ ] Create PointOfWork, POWConnection, OrderLegRoute models
- [ ] Implement RoutingService (Dijkstra/A*, decompose orders into legs)
- [ ] Add API: `GET /api/pow-connections/`, `POST /api/calculate-route`
- [ ] **Test:** Order decomposed into 3 legs (Yard → Transfer → Quay); path validated

#### **Sprint 1.4: Dispatching (Week 2.5)**
- [ ] Create DispatchingPolicy, ChePool, DispatchingQueue, CheCurrentTask models
- [ ] Implement DispatchingService (availability, scoring, selection)
- [ ] Add CPU-periodic task: `batch_dispatch_worker()` every 2 mins (or use Celery)
- [ ] Add API: `POST /api/dispatch-order-leg`, `POST /api/dispatch-batch`
- [ ] **Test:** Order leg auto-assigned to closest available CHE; 5-leg batch dispatch works

#### **Sprint 1.5: Planning & Planning App (Week 3)**
- [ ] Create ProductivityProfile, PlanningPreferences, WorkOrderSequence models
- [ ] Implement PlanningService (DSCH/LOAD WorkQueue creation, sequencing, time estimation)
- [ ] Add API: `POST /api/plan-dsch`, `POST /api/plan-load`
- [ ] **Test:** Vessel with 50 DSCH containers → 5 WorkQueues created, time-sequenced

#### **Sprint 1.6: Update APIs for Simulator (Week 3.5)**
- [ ] Add API: `POST /api/che-status-update`, `POST /api/order-leg-status-update`, `POST /api/manual-order-update`
- [ ] Extend models with status fields
- [ ] Implement EventProcessingService
- [ ] **Test:** Simulator sends pickup/delivery confirmations → Order leg marked complete

#### **Sprint 1.7: Integration & Documentation (Week 4)**
- [ ] Create comprehensive API documentation (OpenAPI/Swagger)
- [ ] Create data flow diagrams (EDI → Visit → Plan → Dispatch → Execution)
- [ ] Add Django management commands for seeding demo data (vessels, containers, POWs)
- [ ] **Test with Godot:** Full end-to-end scenario: Upload EDI → Auto-plan → Auto-dispatch → Execute & confirm

---

### **Phase 2: Optimization & Advanced Features (Weeks 5-8)**
*Goal: Smart algorithms, rehandling, monitoring dashboard*

#### **Sprint 2.1: Advanced Yard Planning (Week 5)**
- [ ] Implement ML-based container classification (extend YardPlanningService)
- [ ] Add rehandling logic (replan containers for extraction)
- [ ] Add predictive positioning (pre-stage LOAD containers)
- [ ] **Test:** Rehandled container repositioned optimally; LOAD containers pre-staged

#### **Sprint 2.2: Equipment Pools & Load Balancing (Week 5.5)**
- [ ] Implement ChePool logic (pool-aware dispatching)
- [ ] Add weighted dispatching policies (distance, fuel, utilization)
- [ ] Add multi-che-type support (e.g., RTG for yard, TT for transfer)
- [ ] **Test:** LOAD move assigned to STS (not RTG); RTG pool balanced

#### **Sprint 2.3: Monitoring & Analytics (Week 6)**
- [ ] Create DashboardMetrics model (cache productivity, delays, status)
- [ ] Implement aggregation service (che_stats, order_stats, yard_health)
- [ ] Add API: `GET /api/dashboard-metrics/`, `GET /api/che-efficiency`, `GET /api/yard-health`
- [ ] Add WebSocket for real-time updates (optional, or HTTP polling)
- [ ] **Test:** Dashboard shows live CHE positions, move counts, fuel usage

#### **Sprint 2.4: Restow Detection & Handling (Week 6.5)**
- [ ] Add restow logic to planning service
- [ ] Create RestowOrder model if needed
- [ ] Trigger restow planning when blocking container is moved
- [ ] **Test:** Container moved → all overlayed containers auto-planned for restow

#### **Sprint 2.5: Capacity Planning & Slot Reservation (Week 7)**
- [ ] Implement slot reservation (reserve positions for late-arriving containers)
- [ ] Add capacity alerts (block 80% full → warn dispatcher)
- [ ] Implement spillover logic (overflow to secondary block if primary full)
- [ ] **Test:** Slot reserved 1 hour in advance; spillover triggered when needed

---

### **Phase 3: Simulator & MES Integration (Weeks 9-12)**
*Goal: Full MQTT/SparkPlugB integration, equipment telemetry, fault simulation*

#### **Sprint 3.1: Godot Simulator - Event Loop (Week 9)**
- [ ] Implement task acceptance/rejection logic in Godot
- [ ] Add pickup/delivery confirmation logic
- [ ] Hook MQTT/API to dispatch messages
- [ ] **Test:** Dispatch → Godot accepts → Move simulated → Pickup confirmed → TOS updated

#### **Sprint 3.2: Godot Simulator - Equipment Faults (Week 9.5)**
- [ ] Implement random fault injection (RTG breakdown, 5-10 min auto-recovery)
- [ ] Send fault events to TOS (`POST /api/che-fault`)
- [ ] Auto-reject all pending tasks for faulty CHE
- [ ] **Test:** RTG faults → orders reassigned to backup; recovered → online again

#### **Sprint 3.3: MQTT/SparkPlugB Integration (Week 10)**
- [ ] Set up Ignition/MES MQTT topics
- [ ] Define SparkPlugB schema for CHE (position, fuel, status, metrics)
- [ ] Implement MQTT message parsing in TOS (or relay through MES)
- [ ] Add MQTT consumer service (background task)
- [ ] **Test:** Godot sends MQTT → TOS receives position + fuel updates

#### **Sprint 3.4: MES Telemetry Model (Week 10.5)**
- [ ] Create CheTelemetry model (timestamp, position, fuel, spreader_size, load_weight, etc.)
- [ ] Create EquipmentMetrics aggregation (efficiency, fuel consumption, availability %)
- [ ] Add API: `GET /api/ches/{id}/telemetry`, `GET /api/equipment-metrics`
- [ ] **Test:** MES sends telemetry → Stored & queryable

#### **Sprint 3.5: Full End-to-End Test (Week 11)**
- [ ] Create test scenario: Simulate full vessel discharge (50 DSCH moves)
- [ ] Verify entire flow: EDI → Plan → Dispatch → Execute → Monitor → Complete
- [ ] Measure metrics: total_time, avg_move_time, equipment_utilization, fuel_consumed
- [ ] **Test:** Report generated with all KPIs

#### **Sprint 3.6: Documentation & Knowledge Transfer (Week 12)**
- [ ] Create architecture diagram (MiniTOS ↔ Godot ↔ MES)
- [ ] Document MQTT topic schema
- [ ] Create troubleshooting guide
- [ ] Prepare for Phase 4 (LLM/RL agents)

---

### **Phase 4: AI & Advanced Optimization (Future, Weeks 13+)**
*Goal: ML-based planning, RL dispatching agent, predictive maintenance*

- Implement RL agent for dynamic dispatching (state=CHE status, action=assign, reward=makespan reduction)
- Add LLM for natural language planning queries ("Plan 50 DSCH moves from Bay 10-15")
- Implement predictive maintenance (ML model for fault prediction)
- Add scenario planning ("What if RTG breaks down?")

---

## 4. DETAILED DATA MODEL SUMMARY (All Changes)

### **New Models (14 total)**
1. **EDIFile** - Uploaded manifest metadata
2. **EDIContainer** - Individual container from EDI line
3. **TransportVisit** - Generic visit entity (replaces/extends Visit)
4. **VesselVisit** - Vessel-specific visit (extends TransportVisit)
5. **TransportVisitContainer** - Explicit container-visit mapping with sequence
6. **YardCargoCategory** - Cargo classification hierarchy
7. **YardPositionAssignment** - Pile → YCC mapping
8. **ContainerClassification** - Container → YCC matching audit
9. **ContainerYardAssignment** - Placement decision audit trail
10. **PointOfWork** - Terminal touchpoints (Blocks, QCs, Gates, etc.)
11. **POWConnection** - Network edges between POWs
12. **OrderLegRoute** - Explicit route for each order leg
13. **DispatchingPolicy** - Algorithm selection & weights
14. **ChePool** - Equipment logical grouping
15. **DispatchingQueue** - Pending assignment tracking
16. **CheCurrentTask** - Active task per CHE
17. **ProductivityProfile** - Equipment productivity rates
18. **PlanningPreferences** - Terminal planning strategy
19. **WorkOrderSequence** - Explicit timing for orders
20. **CheTelemetry** - Equipment sensor data (optional, for Phase 3)

### **Extended Models (Adds fields/logic)**
1. **ContainerVisit** - Add status, position tracking, last_updated
2. **Che** - Ensure status used, add last_position, last_update_time, metrics
3. **OrderLeg** - Add status, timestamps, actual measurements, failure_reason
4. **Order** - Add status, completion tracking
5. **Block** - No changes needed (already auto-generates piles)
6. **Position** - Already has validation; ensure fully used
7. **Visit** → **TransportVisit** (refactor)

---

## 5. API SUMMARY (By Feature)

### **EDI & Planning: 10 endpoints**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/edi/upload` | POST | Upload EDI file |
| `/api/transport-visits` | GET/POST | CRUD transport visits |
| `/api/transport-visits/{id}` | PATCH | Update visit status |
| `/api/transport-visits/{id}/containers` | GET | List containers + sequence |
| `/api/plan-dsch` | POST | Plan discharge moves |
| `/api/plan-load` | POST | Plan load moves |
| `/api/planning-preferences` | GET/POST/PATCH | Strategy config |
| `/api/planning-status` | GET | Progress tracking |

### **Yard Planning: 8 endpoints**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/yard-cargo-categories` | GET/POST/PATCH | YCC CRUD |
| `/api/yard-position-assignments` | GET/POST | Pile-YCC mapping |
| `/api/container-recommendations` | GET | Get position suggestions (top 1 or N) |
| `/api/override-assignment` | PATCH | Manual override |
| `/api/yard-health-check` | GET | Verify all containers categorized |

### **Routing: 6 endpoints**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/pows` | GET/POST | CRUD point of work |
| `/api/pow-connections` | GET/POST | CRUD POW network |
| `/api/calculate-route` | POST | Find shortest path (with alternatives) |
| `/api/calculate-legs` | POST | Decompose order into legs |

### **Dispatching: 8 endpoints**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/dispatching-policies` | GET/POST | Policy CRUD |
| `/api/che-pools` | GET/POST | Pool CRUD |
| `/api/dispatching-queue` | GET | View pending orders |
| `/api/dispatch-order-leg` | POST | Assign single order leg |
| `/api/dispatch-batch` | POST | Assign multiple legs (called every 2 mins) |
| `/api/ches/{id}/accept-task` | POST | CHE accepts dispatch |
| `/api/ches/{id}/reject-task` | POST | CHE rejects dispatch |
| `/api/ches/{id}/confirm-pickup` | POST | Task pickup confirmed |
| `/api/ches/{id}/confirm-delivery` | POST | Task delivery confirmed |

### **Monitoring: 10 endpoints**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/ches/{id}/update-status` | POST | Update CHE status |
| `/api/ches/{id}/update-position` | POST | Update CHE location |
| `/api/ches/{id}/log-fault` | POST | Log equipment fault |
| `/api/container-visits/{id}/update-status` | POST | Update container status |
| `/api/container-visits/{id}/confirm-arrival` | POST | Container arrived |
| `/api/orders/{id}/leg-update` | POST | Update order leg status |
| `/api/manual-order-update` | POST | Handle move without prior order |
| `/api/batch-status-updates` | POST | Bulk update multiple entities |
| `/api/dashboard-metrics` | GET | Overall dashboard KPIs |
| `/api/che-efficiency` | GET | CHE performance metrics |

---

## 6. CRITICAL IMPLEMENTATION CONSIDERATIONS

### **A. Concurrency & Race Conditions**
- **Issue:** Dispatcher assigns same CHE to 2 orders simultaneously
- **Solution:** Use Django ORM `F()` expressions & `select_for_update()` to lock CHE row during assignment
- **Code:**
  ```python
  selected_che = Che.objects.select_for_update().get(id=che_id)
  # Now safe to assign without race condition
  ```

### **B. Time Synchronization**
- **Issue:** Simulator clock ≠ TOS clock (network latency, drift)
- **Solution:** Timestamp all events on sender; server stores both send_time and receive_time
- **Model:**
  ```python
  OrderLeg:
    pickup_time_reported = DateTimeField  # When CHE reported pickup
    pickup_time_received = DateTimeField(auto_now=True)  # When TOS received
  ```

### **C. Idempotency**
- **Issue:** TCP retry sends duplicate status update; container counted twice as "delivered"
- **Solution:** Use unique constraint on (order_leg, event_type). Updates are upserts, not appends.
- **Code:**
  ```python
  class OrderLegStatusLog(models.Model):
    order_leg = ForeignKey(OrderLeg)
    event_type = CharField(choices=[PickupConfirmed, DeliveryConfirmed])
    class Meta:
      unique_together = ('order_leg', 'event_type')
    # Upsert: django.db.models.Q update or create
  ```

### **D. Cascade Deletes**
- **Issue:** Deleting TransportVisit cascades → all Orders deleted; data loss
- **Solution:** Use `on_delete=PROTECT` or `SET_NULL` (not CASCADE) for sensitive FKs
- **Current Code Issues:**
  ```python
  # BAD: cascades too aggressively
  location = ForeignKey('Location', on_delete=models.CASCADE)
  
  # GOOD:
  location = ForeignKey('Location', on_delete=models.PROTECT)
  # Forces manual handling of referenced records
  ```

### **E. State Machine Validation**
- **Issue:** Invalid transition (Complete → Announced) silently allowed
- **Solution:** Implement state manager with transition validator
- **Code:**
  ```python
  class TransportVisitStateManager:
    TRANSITIONS = {
      'Planned': ['Announced'],
      'Announced': ['Arrived'],
      'Arrived': ['InOperation'],
      'InOperation': ['Complete'],
      'Complete': ['Departed'],
    }
    
    def validate_transition(self, from_state, to_state):
      if to_state not in self.TRANSITIONS.get(from_state, []):
        raise ValidationError(f"Cannot go from {from_state} to {to_state}")
  ```

### **F. Algorithmic Performance**
- **Issue:** Pathfinding (Dijkstra) on 1000-POW network = slow
- **Solution:** Cache POW network graph; recompute hourly or on network change
- **Code:**
  ```python
  class POWNetworkCache:
    def __init__(self, location):
      self.graph = self.build_network_graph(location)
      self.last_updated = now()
    
    def is_stale(self):
      return (now() - self.last_updated).total_seconds() > 3600
    
    def find_path(self, start, end):
      if self.is_stale():
        self.graph = self.build_network_graph()
        self.last_updated = now()
      return dijkstra(self.graph, start, end)
  ```

### **G. Background Tasks (Periodic Dispatching)**
- **Issue:** Dispatcher must run every 2 minutes; Django views are request-only
- **Solution:** Use Celery + Redis for async task scheduling
- **Code (in tasks.py):**
  ```python
  from celery import shared_task
  from .services import DispatchingService
  
  @shared_task
  def periodic_batch_dispatch():
    for location in Location.objects.filter(is_active=True):
      DispatchingService.batch_dispatch_worker(location)
  
  # in settings.py:
  CELERY_BEAT_SCHEDULE = {
    'batch-dispatch-every-2-mins': {
      'task': 'minitos_core.tasks.periodic_batch_dispatch',
      'schedule': crontab(minute='*/2'),
    }
  }
  ```

### **H. MQTT Integration**
- **Issue:** Godot sends MQTT; TOS needs to consume messages
- **Solution:** Django middleware + MQTT client library (paho-mqtt)
- **Setup:**
  ```python
  # settings.py
  INSTALLED_APPS += ['django_mqtt_client']
  MQTT_BROKER_HOST = 'localhost'
  MQTT_BROKER_PORT = 1883
  MQTT_TOPICS = ['/minitos/+/che/update', '/minitos/+/order/complete']
  ```

---

## 7. TESTING STRATEGY

### **Unit Tests (40% of effort)**
- YCCHierarchyManager.find_matching_ycc() → test hierarchy traversal
- RoutingService.find_shortest_path() → test Dijkstra on mock POW network
- DispatchingService.score_che_for_leg() → test scoring formula
- EDIProcessor.parse_edi_file() → test EDI format parsing

### **Integration Tests (40% of effort)**
- End-to-end: Upload EDI → Create Visit → Plan DSCH → Dispatch → Confirm
- Multi-leg order: Verify Order decomposed into 3+ OrderLegs correctly
- Replan scenario: Container moved → re-optimized placement triggers
- Failure handling: CHE rejects task → reassigned to next CHE

### **Load Tests (20% of effort)**
- Dispatch 1000 order legs in batch → performance measurement
- MQTT subscribe to 100 telemetry updates/sec → throughput
- Pathfinding on 1000-POW network → latency (should be <100ms cached)

### **Acceptance Criteria**
✅ [Phase 1 complete] EDI upload for 50-container vessel → 5 sequenced WorkQueues in <5 secs  
✅ [Phase 1 complete] Dispatch 100 orders to available CHE pool → 100% assigned (or NoCHEAvailable logged)  
✅ [Phase 2 complete] Godot simulator executes full DSCH vessel discharge without manual intervention  
✅ [Phase 3 complete] MQTT telemetry received every 10 secs; no dropped messages  
✅ [Phase 4 ready] RL agent baseline performance: 5% improvement over heuristic dispatcher  

---

## 8. RISK & MITIGATION

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Scheduling ambiguity:** "2-minute dispatch horizon" unclear when to run | HIGH | Define in PlanningPreferences: batch_dispatch_interval_minutes (default 2). Celery beat job configurable. |
| **YCC hierarchy complexity:** Too many categories → slow classification | MEDIUM | Implement caching; add max_depth limit (3 levels); use Redis for YCC lookup. |
| **POW network maintenance:** Adding/removing POWs invalidates cached paths | MEDIUM | Audit trail: track POW changes; rebuild cache on update; test alerts. |
| **Simulator MQTT connectivity:** Godot can't reach broker | HIGH | Add health check endpoint `/api/health/mqtt-status`; alerts if no messages for 5 mins. |
| **State machine confusion:** Developers accidentally allow invalid transitions | MEDIUM | Unit tests covering all transitions; make state manager enforce strictly (raise exception). |
| **Scalability:** 10K orders to plan → timeout | MEDIUM | Implement pagination; batch processing; async task queue (Celery). |

---

## 9. SUCCESS METRICS (MVP Definition)

**By end of Phase 1:**
- ✅ 50-container vessel EDI → auto-planned in <5 seconds
- ✅ 100% of orders dispatched to CHE (or properly rejected with reason)
- ✅ 0 manual interventions for typical vessel discharge workflow
- ✅ All containers assigned to valid yard position per YCC rules

**By end of Phase 3:**
- ✅ Full end-to-end execution: EDI → Plan → Dispatch → Simulator → Confirm → Complete
- ✅ Equipment faults simulated & handled (auto-reassignment)
- ✅ MQTT telemetry flowing: position, fuel, status every 10 secs
- ✅ Dashboard showing real-time KPIs: moves/hour, CHE utilization, delays

---

## 10. RECOMMENDED NEXT STEPS

1. **Review & Feedback** (This week)
   - Stakeholder sign-off on models, APIs, algorithm descriptions
   - Adjust EDI parser format (BAPLIE? 424? Custom JSON?)
   - Confirm productivity rates for equipment types

2. **Kick-off Sprint 1.1** (Next week)
   - Assign sprint leads
   - Create database migration stubs
   - Set up Celery for background tasks
   - Begin EDI parser implementation

3. **Demo Schedule**
   - EOW1: EDI upload + TransportVisit created
   - EOW2: DSCH planning + WorkQueues with timed sequences
   - EOW3: Routing + Dispatching (orders assigned to CHE)
   - EOW4: Simulator integration (orders execute in Godot)

---

**Document Version:** 1.0  
**Last Updated:** March 7, 2026  
**Status:** Ready for Implementation

