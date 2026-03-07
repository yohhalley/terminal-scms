# MiniTOS MVP Implementation Checklist

Quick reference for executing the roadmap laid out in FUNCTIONALITY_GAPS_AND_ROADMAP.md

---

## Phase 1: Foundation (Weeks 1-4)

### Sprint 1.1: EDI & Visit Lifecycle (Week 1)

#### Models to Create
- [ ] **EDIFile** (`minitos_core/models.py`)
  ```python
  class EDIFile(models.Model):
    filename = CharField(max_length=255)
    upload_date = DateTimeField(auto_now_add=True)
    STATUS_CHOICES = [('Pending', 'Pending'), ('Processing', 'Processing'), ...]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_by = ForeignKey(User, ...)
    error_log = TextField(blank=True, null=True)
  ```

- [ ] **EDIContainer** (maps to container manifest lines)
- [ ] **TransportVisit** (rename/extend Visit)
  - Status: Planned, Announced, Arrived, InOperation, Complete, Departed
  - Fields: visit_type, eta, etd, location
  
- [ ] **VesselVisit** (extends TransportVisit)
  - Fields: vessel_imo, vessel_name, berth

- [ ] **TransportVisitContainer** (explicit mapping)
  - Fields: transport_visit, container_visit, sequence, move_type

#### Services to Create
- [ ] `minitos_core/services/edi_processor.py`
  - `parse_edi_file(file_path) → List[EDILine]`
  - `validate_edi_line(line) → (is_valid, errors)`
  - `check_duplicate_visit(visit_name) → bool`
  - `create_container_visits_from_edi(edi_lines, transport_visit)`

- [ ] `minitos_core/services/visit_state_manager.py`
  - `TransportVisitStateManager` class
  - Transition validation logic
  - Hooks: on_visit_arrived, on_visit_departed

#### Views/APIs to Add
- [ ] `minitos_core/views.py`: Add `EDIUploadView` (POST /api/edi/upload)
- [ ] `minitos_core/views.py`: Add `TransportVisitViewSet`
- [ ] `minitos_core/urls.py`: Register new endpoints

#### Serializers
- [ ] `minitos_core/serializers.py`: `EDIFileSerializer`, `TransportVisitSerializer`

#### Migrations
- [ ] Create and apply migrations for 4 new models
  - `python manage.py makemigrations`
  - `python manage.py migrate`

#### Tests
- [ ] `minitos_core/tests.py`:
  - [ ] Test EDI file upload & parsing (50 containers)
  - [ ] Test duplicate visit detection
  - [ ] Test TransportVisit status transitions
  - [ ] Test ContainerVisits created in batch

#### Demo
- [ ] Upload sample EDI file with 50 containers → verify TransportVisit created + 50 ContainerVisits in "Preadvised" status

---

### Sprint 1.2: Yard Cargo Categories & Assignment (Week 1.5)

#### Models to Create
- [ ] **YardCargoCategory** (YCC hierarchy)
  ```python
  class YardCargoCategory(models.Model):
    name = CharField(max_length=100)
    code = CharField(max_length=20, unique=True)
    parent_category = ForeignKey('self', null=True, blank=True, ...)
    requires_power = BooleanField(default=False)
    is_imdg = BooleanField(default=False)
    requires_ventilation = BooleanField(default=False)
    rules = JSONField(default=dict)  # Match criteria
  ```

- [ ] **YardPositionAssignment**
  ```python
  class YardPositionAssignment(models.Model):
    pile = ForeignKey(Pile, ...)
    yard_cargo_category = ForeignKey(YardCargoCategory, ...)
    priority = IntegerField(default=5)
    max_stacking_height = IntegerField(null=True)
    effective_from = DateTimeField()
    effective_to = DateTimeField(null=True)
  ```

- [ ] **ContainerClassification**
  ```python
  class ContainerClassification(models.Model):
    container_visit = ForeignKey(ContainerVisit, ...)
    yard_cargo_category = ForeignKey(YardCargoCategory, ...)
    confidence = FloatField(default=1.0)  # 0-1
    matched_rules = JSONField(default=list)
    created_at = DateTimeField(auto_now_add=True)
  ```

- [ ] **ContainerYardAssignment**
  ```python
  class ContainerYardAssignment(models.Model):
    container_visit = OneToOneField(ContainerVisit, ...)
    position = ForeignKey(Position, ...)
    assigned_by = CharField(max_length=20, choices=[('Manual', 'Manual'), ('Algorithm', 'Algorithm'), ...])
    algorithm_score = FloatField(null=True)
    assigned_at = DateTimeField(auto_now_add=True)
    reason = TextField()
  ```

#### Services to Create
- [ ] `minitos_core/services/yard_planning_service.py`
  - `classify_container(container_visit) → YardCargoCategory`
  - `search_available_positions(ycc, location) → List[Position]`
  - `optimize_position(container_visit, positions, move_type) → List[(Position, score)]`
  - `assign_position(container_visit, position, reason) → ContainerYardAssignment`
  - `validate_yard_health() → {all_categorized: bool, uncategorized: List}`

- [ ] `minitos_core/services/ycc_hierarchy_manager.py`
  - `get_hierarchy_tree() → Dict`
  - `find_matching_ycc(container_attributes) → List[YCC]`
  - `get_fallback_ycc() → YCC`

#### Views/APIs to Add
- [ ] `GET /api/yard-cargo-categories/` (list all YCC)
- [ ] `POST /api/yard-cargo-categories/` (create YCC)
- [ ] `GET /api/locations/{location_pk}/yard-position-assignments/`
- [ ] `POST /api/locations/{location_pk}/yard-position-assignments/`
- [ ] `POST /api/locations/{location_pk}/container_visits/{visit_pk}/assign-yard-position`
  - Input: container_visit_id, optional preferred_position
  - Output: ContainerYardAssignment + recommended positions
- [ ] `GET /api/locations/{location_pk}/container_visits/{visit_pk}/position-recommendations`
  - Optional query param: limit (default 1, max 10)
- [ ] `PATCH /api/locations/{location_pk}/container_visits/{visit_pk}/override-assignment`
- [ ] `GET /api/yard/health-check`

#### Serializers
- [ ] `YardCargoCategorySerializer`, `YardPositionAssignmentSerializer`, `ContainerClassificationSerializer`, `ContainerYardAssignmentSerializer`

#### Migrations
- [ ] Create migrations for 4 new models

#### Tests
- [ ] Test YCC hierarchy traversal (3-level hierarchy)
- [ ] Test container classification (matching to YCC rules)
- [ ] Test position search (find all available positions for YCC)
- [ ] Test position optimization (scoring formula)
- [ ] Test assignment (manual + algorithmic)
- [ ] Test yard health check (all containers categorized)

#### Demo
- [ ] Create 3-level YCC hierarchy (General → Reefer → Reefer 20ft)
- [ ] Assign 10 containers to yard → verify positions auto-assigned per YCC
- [ ] Override one assignment manually → verify audit trail

---

### Sprint 1.3: Routing System (Week 2)

#### Models to Create
- [ ] **PointOfWork (POW)**
  ```python
  class PointOfWork(models.Model):
    name = CharField(max_length=100)
    pow_type = CharField(max_length=20, choices=[('StorageBlock', ...), ('QC', ...), ...])
    location = ForeignKey(Location, ...)
    code = CharField(max_length=20)
    x_position = FloatField(null=True)
    y_position = FloatField(null=True)
    capacity = IntegerField(null=True)
    is_active = BooleanField(default=True)
    class Meta:
      unique_together = ('location', 'code')
  ```

- [ ] **POWConnection**
  ```python
  class POWConnection(models.Model):
    from_pow = ForeignKey(PointOfWork, related_name='outgoing', ...)
    to_pow = ForeignKey(PointOfWork, related_name='incoming', ...)
    distance = IntegerField()  # meters
    throughput_capacity = IntegerField()  # containers/hour
    allowed_che_types = ManyToManyField(EquipmentType)
    is_bidirectional = BooleanField(default=False)
    cost = FloatField(default=1.0)  # for pathfinding
    effective_from = DateTimeField()
    effective_to = DateTimeField(null=True)
    class Meta:
      unique_together = ('from_pow', 'to_pow')
  ```

- [ ] **OrderLegRoute**
  ```python
  class OrderLegRoute(models.Model):
    order_leg = ForeignKey(OrderLeg, ...)
    pows = ManyToManyField(PointOfWork, through='OrderLegRoutePOW')
    estimated_travel_time = IntegerField()  # seconds
    estimated_distance = IntegerField()  # meters
    created_at = DateTimeField(auto_now_add=True)
  
  class OrderLegRoutePOW(models.Model):
    order_leg_route = ForeignKey(OrderLegRoute, ...)
    pow = ForeignKey(PointOfWork, ...)
    sequence = IntegerField()
  ```

#### Services to Create
- [ ] `minitos_core/services/routing_service.py`
  - `build_network_graph(location) → Graph`
  - `find_shortest_path(from_pow, to_pow, che_type, location) → List[POW]`
  - `find_alternative_routes(from_pow, to_pow, che_type, location, limit=3) → List[List[POW]]`
  - `decompose_order_into_legs(order) → List[OrderLeg]`
  - `calculate_leg_distance(leg) → Int`
  - `get_pow_for_pile(pile, location) → POW`
  - `POWNetworkCache` class (cache graph, recompute on changes)

#### Views/APIs to Add
- [ ] `GET /api/locations/{location_pk}/pows/`
- [ ] `POST /api/locations/{location_pk}/pows/`
- [ ] `GET /api/locations/{location_pk}/pow-connections/`
- [ ] `POST /api/locations/{location_pk}/pow-connections/`
- [ ] `POST /api/locations/{location_pk}/calculate-route`
  - Input: { from_pow_id, to_pow_id, che_type }
  - Output: { route, distance, travel_time, alternatives }
- [ ] `POST /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/calculate-legs`
  - Input: Order data
  - Output: List[OrderLeg] with routes

#### Serializers
- [ ] `PointOfWorkSerializer`, `POWConnectionSerializer`, `OrderLegRouteSerializer`

#### Migrations
- [ ] Create migrations for 3 new models

#### Tests
- [ ] Build POW network (5 POWs, 8 connections)
- [ ] Test Dijkstra pathfinding (shortest path Block1 → QC1)
- [ ] Test alternative routes (find 3 alternative paths)
- [ ] Test order decomposition (1 Order → 3 OrderLegs with routes)
- [ ] Test path caching (verify same path returned twice without recomputation)

#### Demo
- [ ] Create POW network with 5 points (Block1, Block1_TP, QC1_TP, QC1, Gate)
- [ ] Calculate route from Block1 to QC1 → verify path = [Block1, Block1_TP, QC1_TP, QC1]
- [ ] Decompose Order (Block1_Bay01_Tier01 → QC1) → verify 3-leg OrderLegs created

---

### Sprint 1.4: Dispatching (Week 2.5)

#### Models to Create
- [ ] **DispatchingPolicy**
  ```python
  class DispatchingPolicy(models.Model):
    name = CharField(max_length=100)
    location = ForeignKey(Location, ...)
    algorithm = CharField(max_length=20, choices=[('NearestFirst', ...), ('LeastBusy', ...), ...])
    weight_distance = FloatField(default=0.5)
    weight_utilization = FloatField(default=0.3)
    weight_fuel = FloatField(default=0.2)
    max_dispatch_horizon_minutes = IntegerField(default=120)
    is_active = BooleanField(default=True)
  ```

- [ ] **ChePool**
  ```python
  class ChePool(models.Model):
    name = CharField(max_length=100)
    location = ForeignKey(Location, ...)
    equipment_types = ManyToManyField(EquipmentType)
    pows_covered = ManyToManyField(PointOfWork)
    max_parallel_orders = IntegerField(default=1)
  ```

- [ ] **DispatchingQueue**
  ```python
  class DispatchingQueue(models.Model):
    order_leg = ForeignKey(OrderLeg, ...)
    created_at = DateTimeField(auto_now_add=True)
    STATUS_CHOICES = [('Pending', ...), ('Assigned', ...), ('Rejected', ...), ('Cancelled', ...)]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    assigned_che = ForeignKey(Che, null=True, blank=True, ...)
    assigned_at = DateTimeField(null=True)
    rejection_reason = TextField(blank=True)
    priority = IntegerField(default=5)
  ```

- [ ] **CheCurrentTask**
  ```python
  class CheCurrentTask(models.Model):
    che = ForeignKey(Che, ...)
    order_leg = ForeignKey(OrderLeg, ...)
    STATUS_CHOICES = [('Assigned', ...), ('PickupConfirmed', ...), ('DeliveryConfirmed',  ...), ('Rejected', ...)]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default='Assigned')
    assigned_at = DateTimeField(auto_now_add=True)
    pickup_time = DateTimeField(null=True)
    delivery_time = DateTimeField(null=True)
  ```

#### Services to Create
- [ ] `minitos_core/services/dispatching_service.py`
  - `get_available_che_for_leg(order_leg) → List[Che]`
  - `score_che_for_leg(che, order_leg, policy) → Float`
  - `select_best_che(available_che, order_leg, policy) → Che`
  - `dispatch_order_leg(order_leg, preferred_che=None, policy=None) → (assigned_che, score)`
  - `dispatch_batch(order_leg_ids, policy) → List[(order_leg_id, assigned_che, score)]`
  - `batch_dispatch_worker(location, time_now) → Dict`

- [ ] `minitos_core/services/che_task_manager.py`
  - `assign_task_to_che(che, order_leg) → CheCurrentTask`
  - `accept_task(che, order_leg) → CheCurrentTask`
  - `reject_task(che, order_leg, reason) → DispatchingQueue`
  - `complete_task(che, order_leg) → CheCurrentTask`
  - `get_che_current_load(che) → Int`

#### Views/APIs to Add
- [ ] `GET /api/dispatching-policies/`
- [ ] `POST /api/dispatching-policies/`
- [ ] `GET /api/locations/{location_pk}/che-pools/`
- [ ] `POST /api/locations/{location_pk}/che-pools/`
- [ ] `GET /api/locations/{location_pk}/dispatching-queue`
- [ ] `POST /api/locations/{location_pk}/dispatch-order-leg`
- [ ] `POST /api/locations/{location_pk}/dispatch-batch`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/accept-task`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/reject-task`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/confirm-pickup`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/confirm-delivery`

#### Celery Tasks
- [ ] `minitos_core/tasks.py`
  ```python
  from celery import shared_task
  
  @shared_task
  def periodic_batch_dispatch():
    for location in Location.objects.filter(is_active=True):
      DispatchingService.batch_dispatch_worker(location, now())
  ```

- [ ] `settings.py`: Add Celery beat scheduler
  ```python
  CELERY_BEAT_SCHEDULE = {
    'batch-dispatch-every-2-mins': {
      'task': 'minitos_core.tasks.periodic_batch_dispatch',
      'schedule': crontab(minute='*/2'),
    }
  }
  ```

#### Serializers
- [ ] `DispatchingPolicySerializer`, `ChePoolSerializer`, `DispatchingQueueSerializer`, `CheCurrentTaskSerializer`

#### Migrations
- [ ] Create migrations for 4 new models

#### Tests
- [ ] Test CHE availability filtering (status, current_tasks)
- [ ] Test scoring formula (distance, utilization, fuel)
- [ ] Test single order leg dispatch (best CHE selected)
- [ ] Test batch dispatch (10 legs → 10 assigned)
- [ ] Test no CHE available (rejection reason logged)
- [ ] Test periodic worker (runs every 2 mins, processes queue)

#### Demo
- [ ] Create 5 CHEs (3 idle, 2 busy)
- [ ] Dispatch 10 order legs in batch → verify 10 assigned to appropriate CHE
- [ ] Verify pending orders queued → reassigned when CHE becomes available

---

### Sprint 1.5: Planning & Time Estimation (Week 3)

#### Models to Create
- [ ] **ProductivityProfile**
  ```python
  class ProductivityProfile(models.Model):
    location = ForeignKey(Location, ...)
    move_type = CharField(max_length=20, choices=[('DSCH', ...), ('LOAD', ...), ('Yard', ...)])
    che_type = ForeignKey(EquipmentType, ...)
    moves_per_hour = IntegerField()
    moves_per_container = IntegerField(default=1)
    setup_time_minutes = IntegerField(default=0)
    class Meta:
      unique_together = ('location', 'move_type', 'che_type')
  ```

- [ ] **PlanningPreferences**
  ```python
  class PlanningPreferences(models.Model):
    location = ForeignKey(Location, ...)
    dsch_sequence_order = CharField(max_length=20, default='PortToSeaSide')
    load_sequence_order = CharField(max_length=20, default='Custom')
    load_lead_time_minutes = IntegerField(default=10)
    planning_horizon_minutes = IntegerField(default=120)
    replan_threshold_variance = FloatField(default=0.1)  # 10% variance
  ```

- [ ] **WorkOrderSequence** (or use Order.etc field)
  - Optional model if more detailed tracking needed; can use Order.etc

#### Services to Create
- [ ] `minitos_core/services/planning_service.py`
  - `create_workqueues_for_dsch(visit, num_queues=None) → List[WorkQueue]`
    - Group containers by bay → 1 WQ per bay (or custom)
    - Create Order per container
    - Return ordered WorkQueues
  
  - `create_workqueues_for_load(visit, num_queues) → List[WorkQueue]`
    - Create num_queues empty WorkQueues
    - Ready for user to assign containers
  
  - `sequence_orders_by_time(orders, base_time, productivity_profile) → None`
    - Set Order.etc for each order based on sequence
    - Example: 1st order ETC = base_time + 0 mins
    - 2nd order ETC = base_time + 2 mins (assuming 2 min/move)
  
  - `estimate_order_duration(order, productivity_profile) → Int (seconds)`
  - `trigger_yard_planning_for_horizon(location, horizon_minutes) → Dict`
  - `replan_delayed_orders(location, threshold=0.1) → Dict`

- [ ] `minitos_core/services/time_estimation_service.py`
  - `get_productivity_profile(location, move_type, che_type) → ProductivityProfile`
  - `calculate_dsch_sequence_times(visit) → Dict`
  - `calculate_load_sequence_times(visit, lead_time_minutes) → Dict`

#### Views/APIs to Add
- [ ] `POST /api/locations/{location_pk}/transport_visits/{visit_pk}/plan-dsch`
  - Input: { wq_config }
  - Output: List[WorkQueue]

- [ ] `POST /api/locations/{location_pk}/transport_visits/{visit_pk}/plan-load`
  - Input: { wq_config }
  - Output: List[WorkQueue]

- [ ] `GET /api/locations/{location_pk}/planning-preferences`
- [ ] `POST /api/locations/{location_pk}/planning-preferences`
- [ ] `PATCH /api/locations/{location_pk}/planning-preferences/{id}`

- [ ] `GET /api/locations/{location_pk}/planning-status`
  - Output: { visit_id, orders_total, orders_planned, orders_dispatched, orders_completed }

#### Migrations
- [ ] Create migrations for 2 new models

#### Tests
- [ ] Test DSCH WQ creation (50 containers → 5 WQs per bay)
- [ ] Test time sequencing (orders timed with 2-minute intervals)
- [ ] Test LOAD WQ creation (user creates 3 WQs, assigns containers)
- [ ] Test LOAD lead-time (orders timed 10 mins before ops_start)
- [ ] Test productivity profile lookup (DSCH + STS → 30 moves/hour)

#### Demo
- [ ] Plan DSCH for 50-container vessel → 5 WorkQueues created
- [ ] Verify Orders sequenced with 2-minute intervals
- [ ] Verify Yard Planning triggered automatically for each container
- [ ] Verify all containers assigned positions per YCC

---

### Sprint 1.6: Update APIs (Week 3.5)

#### Extend Existing Models
- [ ] **ContainerVisit**
  - Add: status (CharField, choices=[...])
  - Add: last_updated (DateTimeField)

- [ ] **Che**
  - Ensure: status, is_available used
  - Add: last_position (FK to POW)
  - Add: last_update_time (DateTimeField)

- [ ] **OrderLeg**
  - Add: status (CharField)
  - Add: pickup_timestamp, delivery_timestamp (DateTimeField)
  - Add: actual_distance, actual_time (IntegerField)
  - Add: failure_reason (TextField)

- [ ] **Order**
  - Add: status (CharField)

#### Views/APIs to Add
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/update-status`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/update-position`
- [ ] `POST /api/locations/{location_pk}/ches/{che_pk}/log-fault`
- [ ] `POST /api/locations/{location_pk}/container_visits/{visit_pk}/update-status`
- [ ] `POST /api/locations/{location_pk}/container_visits/{visit_pk}/confirm-arrival`
- [ ] `POST /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/leg-update`
- [ ] `PATCH /api/locations/{location_pk}/work_queues/{wq_pk}/orders/{order_pk}/order_legs/{leg_pk}`
- [ ] `POST /api/locations/{location_pk}/manual-order-update`
- [ ] `POST /api/batch-status-updates` (bulk)

#### Services
- [ ] `minitos_core/services/event_processing_service.py`
  - `update_che_status(che_id, status_data) → Che`
  - `update_container_visit_status(container_visit_id, status_data) → ContainerVisit`
  - `update_order_leg_status(order_leg_id, status_data) → OrderLeg`
  - `process_order_completion(order_leg_id) → bool (all legs complete?)`

- [ ] `minitos_core/services/manual_order_handling.py`
  - `detect_manual_move(container_visit_id, from_pos, to_pos) → bool`
  - `create_retroactive_order(container_visit_id, from_pos, to_pos, move_type) → Order`

#### Migrations
- [ ] Create migration adding status fields to existing models

#### Tests
- [ ] Test CHE status update (Idle → Working)
- [ ] Test position update (x, y, pow_id)
- [ ] Test container status update (Preadvised → AtQC)
- [ ] Test order leg completion (pickup + delivery confirm)
- [ ] Test manual order detection & creation
- [ ] Test batch updates (5 events processed)

#### Demo
- [ ] Manually update CHE status via API → verify Godot sees update
- [ ] Order leg confirmed delivered → verify Order marked complete if last leg

---

### Sprint 1.7: Integration & Documentation (Week 4)

#### Documentation
- [ ] **Architecture Diagram** (Mermaid)
  - Show: MiniTOS ↔ Godot ↔ MQTT Broker ↔ MES
  - Data flow: EDI → Plan → Dispatch → Execute

- [ ] **API Documentation** (OpenAPI/Swagger)
  - [x] Generate from Django REST Framework (drf-spectacular or drf-yasg)
  - [ ] Available at `/api/schema/` or `/api/docs/`

- [ ] **Data Model Diagram** (ER diagram)
  - Show: TransportVisit → ContainerVisit → Position ← Pile
  - Show: Order → OrderLeg → Che

- [ ] **State Machine Diagrams** (for Visit, OrderLeg, Che)
  
- [ ] **Algorithm Flowcharts** (for planning, dispatching, routing)

- [ ] **User Guide** (for terminal operator)
  - EDI upload workflow
  - Manual position assignment
  - Dispatch monitoring

#### Django Management Commands
- [ ] `minitos_core/management/commands/seed_demo_data.py`
  - Create 1 location, 5 blocks, 100 piles, 500 positions
  - Create 10 CHEs (RTG, STS, TT)
  - Create 5 ProductivityProfiles
  - Create POW network (10 POWs, 15 connections)

- [ ] `minitos_core/management/commands/create_test_edi.py`
  - Generate sample EDI file (50 containers)

- [ ] `minitos_core/management/commands/demo_workflow.py`
  - Full end-to-end: Upload EDI → Plan → Dispatch → Confirm

#### Configuration
- [ ] `settings.py` additions:
  - Celery configuration
  - MQTT broker settings (for Phase 3)
  - RESTful API pagination defaults
  - Logging config (for debugging)

- [ ] `requirements.txt` additions:
  - celery
  - redis (or rabbitmq for Celery broker)
  - drf-spectacular (for API docs)
  - paho-mqtt (for Phase 3)

#### Testing
- [ ] End-to-end integration test (all 7 sprints combined)
  - [ ] Upload EDI file (50 containers)
  - [ ] Auto-plan DSCH (5 WorkQueues)
  - [ ] Trigger yard assignment (50 containers assigned positions)
  - [ ] Calculate routes (50 orders → ~150 OrderLegs)
  - [ ] Dispatch batch (all 150 legs assigned to CHE)
  - [ ] Confirm execution (simulate pickup + delivery)
  - [ ] Verify Order completion (all orders marked complete)

#### Handoff Documentation
- [ ] **Sprint Transition Document** (to Phase 2 team)
  - Key insights learned
  - Bottlenecks found
  - Recommendations for Phase 2
  - Known issues/TODOs

#### Demo/Presentation
- [ ] **Live Demo** (end-to-end flow)
  - Upload EDI → see 50 containers staged
  - Click "Plan Discharge" → see 5 WorkQueues auto-created
  - Watch dispatch queue → see orders assigned to CHEs over 2 minutes
  - Godot simulator executes moves → TOS receives confirmations
  - Dashboard shows progress: 0/50 → 50/50 complete

---

## Quick Reference: Files to Create/Modify

### New Files
```
minitos_core/
├── services/
│   ├── __init__.py
│   ├── edi_processor.py              # EDI parsing
│   ├── visit_state_manager.py        # Visit lifecycle
│   ├── yard_planning_service.py      # YCC classification + assignment
│   ├── ycc_hierarchy_manager.py      # YCC hierarchy queries
│   ├── routing_service.py            # POW pathfinding
│   ├── dispatching_service.py        # CHE assignment
│   ├── che_task_manager.py           # CHE task lifecycle
│   ├── planning_service.py           # WorkQueue creation
│   ├── time_estimation_service.py    # Productivity models
│   ├── event_processing_service.py   # API update handlers
│   └── manual_order_handling.py      # Retroactive order creation
├── management/commands/
│   ├── seed_demo_data.py
│   ├── create_test_edi.py
│   └── demo_workflow.py
└── tests.py                          # Add 50+ test cases
```

### Modified Files
```
minitos_core/
├── models.py                         # Add 14 new models, extend 5 existing
├── views.py                          # Add 15+ new ViewSets/API views
├── serializers.py                    # Add 14+ serializers
├── urls.py                           # Register new routes
├── signals.py                        # Add signals (e.g., on ContainerVisit created)
├── admin.py                          # Add ModelAdmin for new models
└── migrations/                       # Run makemigrations (auto-generated)

./
├── requirements.txt                  # Add celery, redis, drf-spectacular, paho-mqtt
├── settings.py                       # Celery config, MQTT settings, INSTALLED_APPS
├── tasks.py                          # Celery periodic tasks
└── manage.py                         # (run migrations, management commands)
```

---

## Success Criteria Checklist

### By EOW1 (Sprint 1.1)
- [ ] EDI file upload works (test with 50-container manifest)
- [ ] TransportVisit created in DB with status "Announced"
- [ ] 50 ContainerVisits created in "Preadvised" status
- [ ] No duplicate visit error when re-uploading same EDI

### By EOW2 (Sprint 1.2 + 1.3)
- [ ] YCC hierarchy created (3 levels: General → Type → Size)
- [ ] 50 containers auto-assigned to positions per YCC rules
- [ ] POW network created (5 POWs, 8 connections)
- [ ] Routes calculated successfully (Block1 → QC1 = 3-step path)

### By EOW3 (Sprint 1.4 + 1.5)
- [ ] Service time estimates calculated (2 mins/move for STS)
- [ ] 50 orders sequenced with 100-minute total duration
- [ ] Dispatching batch process assigns 150 OrderLegs to 10 CHEs
- [ ] No more than 1 unassigned order leg (or "NoCHEAvailable" reason logged)

### By EOW4 (Sprint 1.6 + 1.7)
- [ ] Godot simulator sends `/api/ches/{id}/confirm-pickup` → TOS updates
- [ ] OrderLeg marked complete → Order.status updated (if last leg)
- [ ] Full end-to-end: EDI → Plan → Dispatch → Execute → Complete in <10 sec
- [ ] API documentation generated (OpenAPI available)
- [ ] Demo data seeding command works (populated DB in 5 sec)

---

## Dependency Matrix (what must finish before what)

```
1.1 (EDI) 
  └→ 1.2 (Yard Planning) [needs ContainerVisit in DB]
  └→ 1.5 (Planning) [needs TransportVisit to plan from]

1.2 (Yard Planning)
  └→ 1.5 (Planning) [calls YardPlanningService]
  
1.3 (Routing)
  └→ 1.4 (Dispatching) [decompose_order_into_legs uses RoutingService]
  
1.4 (Dispatching)
  └→ 1.6 (Update APIs) [dispatch status updates]
  
1.5 (Planning)
  └→ 1.4 (Dispatching) [created Orders → OrderLegs → Dispatch]

All 1.1-1.6
  └→ 1.7 (Integration & Documentation)
```

**Critical Path: 1.1 → 1.2 → 1.5 → 1.4** (4 weeks)  
**Parallel: 1.3 can start after 1.1** (3 weeks)  
**Integration: 1.6 + 1.7 require all others**

---

## Estimated Effort (Story Points / Person-Days)

| Sprint | Component | Size | Notes |
|--------|-----------|------|-------|
| 1.1 | EDI + Visit Lifecycle | 21 pts | 1 person, 5 days |
| 1.2 | Yard Planning | 34 pts | 1-2 people, 7-8 days (most complex algorithms) |
| 1.3 | Routing System | 21 pts | 1 person, 5 days (Dijkstra implementation) |
| 1.4 | Dispatching | 34 pts | 1-2 people, 7-8 days (scheduling algorithm) |
| 1.5 | Planning Services | 21 pts | 1 person, 5 days |
| 1.6 | Update APIs | 13 pts | 1 person, 3 days |
| 1.7 | Integration + Docs | 13 pts | 1-2 people, 3 days |
| **Total Phase 1** | **157 pts** | **Approx 36 person-days, 4 weeks nominal (can parallelize)** |

---

## Phase 2 Prep (Post-Sprint 1.7)

After Phase 1 demo, prepare Phase 2:
- [ ] Gather performance metrics (EDI parsing speed, pathfinding latency, dispatch throughput)
- [ ] Identify bottlenecks (database queries, algorithm inefficiency, etc.)
- [ ] Collect team feedback on implementation
- [ ] Scope Phase 2 features (advanced YCC, rehandling, load balancing)
- [ ] Assign Phase 2 tech lead

---

**Version:** 1.0  
**Last Updated:** March 7, 2026

