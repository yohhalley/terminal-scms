import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class InboxEvent(models.Model):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (PROCESSED, "Processed"),
        (FAILED, "Failed"),
    ]

    event_id = models.UUIDField(primary_key=True)
    topic = models.CharField(max_length=300)
    schema = models.CharField(max_length=100)
    payload = models.JSONField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["received_at"]


class EnterpriseProjection(models.Model):
    source_id = models.UUIDField(unique=True)
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    source_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SiteProjection(models.Model):
    DRAFT = "draft"
    SYNCING = "syncing"
    READY = "ready"
    ERROR = "error"
    ARCHIVED = "archived"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SYNCING, "Synchronizing"),
        (READY, "Ready"),
        (ERROR, "Error"),
        (ARCHIVED, "Archived"),
    ]

    source_id = models.UUIDField(unique=True)
    enterprise = models.ForeignKey(
        EnterpriseProjection, related_name="sites", on_delete=models.CASCADE,
    )
    source_location_id = models.PositiveBigIntegerField(db_index=True)
    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=120)
    lifecycle_status = models.CharField(max_length=12, default="draft")
    projection_status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=DRAFT,
    )
    source_revision = models.PositiveIntegerField(default=0)
    snapshot_schema = models.CharField(max_length=100, blank=True)
    snapshot_checksum = models.CharField(max_length=64, blank=True)
    snapshot_path = models.CharField(max_length=300, blank=True)
    snapshot_payload = models.JSONField(default=dict)
    simulation_enabled = models.BooleanField(default=True)
    last_reconciled_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    source_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["enterprise__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["enterprise", "code"], name="unique_projected_site_code",
            ),
        ]

    @property
    def is_ready(self):
        return self.projection_status == self.READY

    def __str__(self):
        return f"{self.enterprise.code}/{self.code}"


class SimulationSession(models.Model):
    RUNNING_DESIRED = "running"
    STOPPED_DESIRED = "stopped"
    DESIRED_STATE_CHOICES = [
        (RUNNING_DESIRED, "Running"),
        (STOPPED_DESIRED, "Stopped"),
    ]

    PENDING = "pending"
    CLAIMED = "claimed"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ACTUAL_STATE_CHOICES = [
        (PENDING, "Pending"),
        (CLAIMED, "Claimed"),
        (STARTING, "Starting"),
        (RUNNING, "Running"),
        (STOPPING, "Stopping"),
        (STOPPED, "Stopped"),
        (FAILED, "Failed"),
    ]
    ACTIVE_STATES = [PENDING, CLAIMED, STARTING, RUNNING, STOPPING]

    RUNNER_UNCLAIMED = "unclaimed"
    RUNNER_CLAIMED = "claimed"
    RUNNER_STARTING = "starting"
    RUNNER_RUNNING = "running"
    RUNNER_STOPPED = "stopped"
    RUNNER_FAILED = "failed"
    RUNNER_STATUS_CHOICES = [
        (RUNNER_UNCLAIMED, "Unclaimed"),
        (RUNNER_CLAIMED, "Claimed"),
        (RUNNER_STARTING, "Starting"),
        (RUNNER_RUNNING, "Running"),
        (RUNNER_STOPPED, "Stopped"),
        (RUNNER_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(
        SiteProjection, related_name="simulation_sessions", on_delete=models.PROTECT,
    )
    configuration_revision = models.PositiveIntegerField()
    configuration_checksum = models.CharField(max_length=64)
    bootstrap_payload = models.JSONField(default=dict)
    desired_state = models.CharField(
        max_length=12, choices=DESIRED_STATE_CHOICES, default=RUNNING_DESIRED,
    )
    actual_state = models.CharField(
        max_length=12, choices=ACTUAL_STATE_CHOICES, default=PENDING,
    )
    simulator_version = models.CharField(max_length=80, default="local")
    time_scale = models.FloatField(
        default=1.0, validators=[MinValueValidator(0.01), MaxValueValidator(100.0)],
    )
    sparkplug_group_id = models.CharField(max_length=120)
    sparkplug_node_id = models.CharField(max_length=160)
    runner_identity = models.CharField(max_length=160, blank=True)
    runner_status = models.CharField(
        max_length=12, choices=RUNNER_STATUS_CHOICES, default=RUNNER_UNCLAIMED,
    )
    token_hash = models.CharField(max_length=64, blank=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    claimed_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    stopped_at = models.DateTimeField(blank=True, null=True)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    node_connected = models.BooleanField(default=False)
    sparkplug_bdseq = models.PositiveBigIntegerField(blank=True, null=True)
    sparkplug_last_sequence = models.PositiveSmallIntegerField(blank=True, null=True)
    sequence_gap_count = models.PositiveIntegerField(default=0)
    last_node_birth_at = models.DateTimeField(blank=True, null=True)
    last_node_death_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=models.Q(
                    actual_state__in=["pending", "claimed", "starting", "running", "stopping"],
                ),
                name="one_active_simulation_per_site",
            ),
        ]

    @property
    def is_active(self):
        return self.actual_state in self.ACTIVE_STATES

    def __str__(self):
        return f"{self.site} simulation {self.pk} ({self.actual_state})"


class ExecutionOrder(models.Model):
    DRAFT = "draft"
    PLANNED = "planned"
    RELEASED = "released"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PLANNED, "Planned"),
        (RELEASED, "Released"),
        (IN_PROGRESS, "In progress"),
        (COMPLETE, "Complete"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]

    tos_order_id = models.PositiveBigIntegerField()
    site = models.ForeignKey(
        SiteProjection, related_name="orders", on_delete=models.PROTECT,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=RELEASED)
    priority = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(999)],
    )
    container_visit_id = models.PositiveBigIntegerField(blank=True, null=True)
    from_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    to_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    released_at = models.DateTimeField()
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True)
    source_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "released_at", "tos_order_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "tos_order_id"], name="unique_order_per_site",
            ),
        ]

    def __str__(self):
        return f"TOS order {self.tos_order_id} at {self.site.code}"


class ExecutionOperation(models.Model):
    MOVE = "move"
    HANDLE = "handle"
    HANDOFF = "handoff"
    WAIT = "wait"
    KIND_CHOICES = [
        (MOVE, "Move"),
        (HANDLE, "Handle"),
        (HANDOFF, "Handoff"),
        (WAIT, "Wait"),
    ]
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    WORKING = "working"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PLANNED, "Planned"),
        (DISPATCHED, "Dispatched"),
        (WORKING, "Working"),
        (COMPLETE, "Complete"),
        (FAILED, "Failed"),
        (BLOCKED, "Blocked"),
        (CANCELLED, "Cancelled"),
    ]

    order = models.ForeignKey(
        ExecutionOrder, related_name="operations", on_delete=models.CASCADE,
    )
    tos_leg_id = models.PositiveBigIntegerField()
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=MOVE)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PLANNED)
    required_equipment_type_id = models.PositiveBigIntegerField(blank=True, null=True)
    required_movement_mode = models.CharField(max_length=30, blank=True)
    source_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    destination_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_inventory_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    destination_inventory_position_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_latitude = models.FloatField(blank=True, null=True)
    source_longitude = models.FloatField(blank=True, null=True)
    destination_latitude = models.FloatField(blank=True, null=True)
    destination_longitude = models.FloatField(blank=True, null=True)
    planned_start = models.DateTimeField(blank=True, null=True)
    planned_end = models.DateTimeField(blank=True, null=True)
    actual_start = models.DateTimeField(blank=True, null=True)
    actual_end = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True)
    source_payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["order_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "sequence"], name="unique_operation_sequence_per_order",
            ),
            models.UniqueConstraint(
                fields=["order", "tos_leg_id"], name="unique_tos_leg_per_order",
            ),
        ]

    def __str__(self):
        return f"Order {self.order.tos_order_id} operation {self.sequence}"


class EquipmentState(models.Model):
    SIMULATOR = "simulator"
    MANUAL = "manual"
    IMPORT = "import"
    SOURCE_CHOICES = [
        (SIMULATOR, "Simulator"),
        (MANUAL, "Manual override"),
        (IMPORT, "Imported state"),
    ]

    site = models.ForeignKey(
        SiteProjection, related_name="equipment", on_delete=models.PROTECT,
    )
    tos_che_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=80)
    equipment_type_id = models.PositiveBigIntegerField(blank=True, null=True)
    equipment_type_code = models.CharField(max_length=30, blank=True)
    powered_on = models.BooleanField(default=False)
    working = models.BooleanField(default=False)
    is_available = models.BooleanField(default=False)
    is_dispatched = models.BooleanField(default=False)
    is_in_maintenance = models.BooleanField(default=False)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    rotation = models.FloatField(default=0.0)
    fuel_level = models.FloatField(blank=True, null=True)
    energy_level = models.FloatField(blank=True, null=True)
    speed_mps = models.FloatField(blank=True, null=True)
    state_source = models.CharField(
        max_length=12,
        choices=SOURCE_CHOICES,
        default=SIMULATOR,
    )
    manual_override_at = models.DateTimeField(blank=True, null=True)
    manual_override_by = models.CharField(max_length=150, blank=True)
    source_timestamp = models.DateTimeField(blank=True, null=True)
    last_seen_at = models.DateTimeField()
    simulation_session = models.ForeignKey(
        SimulationSession,
        related_name="equipment_states",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    sparkplug_device_id = models.CharField(max_length=160, blank=True)
    device_connected = models.BooleanField(default=False)
    data_valid = models.BooleanField(default=False)
    sparkplug_last_sequence = models.PositiveSmallIntegerField(blank=True, null=True)
    sparkplug_bdseq = models.PositiveBigIntegerField(blank=True, null=True)
    last_birth_at = models.DateTimeField(blank=True, null=True)
    last_death_at = models.DateTimeField(blank=True, null=True)
    raw_state = models.JSONField(default=dict)

    class Meta:
        ordering = ["site_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "tos_che_id"], name="unique_equipment_per_site",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.site.code})"


class DispatchAssignment(models.Model):
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (ASSIGNED, "Assigned"),
        (ACCEPTED, "Accepted"),
        (EXECUTING, "Executing"),
        (COMPLETE, "Complete"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]

    operation = models.OneToOneField(
        ExecutionOperation, related_name="assignment", on_delete=models.CASCADE,
    )
    equipment = models.ForeignKey(
        EquipmentState, related_name="assignments", on_delete=models.RESTRICT,
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ASSIGNED)
    assigned_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True)


class EquipmentCommand(models.Model):
    PENDING = "pending"
    PUBLISHED = "published"
    ACKNOWLEDGED = "acknowledged"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (PUBLISHED, "Published"),
        (ACKNOWLEDGED, "Acknowledged"),
        (COMPLETE, "Complete"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]

    command_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        DispatchAssignment, related_name="commands", on_delete=models.CASCADE,
    )
    simulation_session = models.ForeignKey(
        SimulationSession,
        related_name="commands",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    command_type = models.CharField(max_length=50)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    topic = models.CharField(max_length=300)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)


class OutboxEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=300)
    schema = models.CharField(max_length=100)
    payload = models.JSONField()
    payload_bytes = models.BinaryField(blank=True, null=True)
    qos = models.PositiveSmallIntegerField(default=1)
    retain = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created_at"]


class CommandResultReceipt(models.Model):
    result_id = models.UUIDField(primary_key=True, editable=False)
    command = models.ForeignKey(
        EquipmentCommand, related_name="result_receipts", on_delete=models.CASCADE,
    )
    event = models.CharField(max_length=40)
    raw_metrics = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_at"]
