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

    tos_order_id = models.PositiveBigIntegerField(unique=True)
    terminal_id = models.PositiveBigIntegerField(db_index=True)
    terminal_code = models.CharField(max_length=80)
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

    def __str__(self):
        return f"TOS order {self.tos_order_id} at {self.terminal_code}"


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

    terminal_id = models.PositiveBigIntegerField(db_index=True)
    terminal_code = models.CharField(max_length=80)
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
    raw_state = models.JSONField(default=dict)

    class Meta:
        ordering = ["terminal_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["terminal_id", "tos_che_id"], name="unique_equipment_per_terminal",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.terminal_code})"


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
    command_type = models.CharField(max_length=50)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    topic = models.CharField(max_length=300)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)


class OutboxEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=300)
    schema = models.CharField(max_length=100)
    payload = models.JSONField()
    qos = models.PositiveSmallIntegerField(default=1)
    retain = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created_at"]
