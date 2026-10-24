from django.contrib import admin

from .models import (
    CommandResultReceipt,
    DispatchAssignment,
    EnterpriseProjection,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    InboxEvent,
    OutboxEvent,
    SimulationSession,
    SiteProjection,
)


@admin.register(SimulationSession)
class SimulationSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "site", "desired_state", "actual_state", "runner_status",
        "configuration_revision", "created_at",
    )
    list_filter = ("desired_state", "actual_state", "runner_status")
    search_fields = ("id", "site__code", "sparkplug_node_id", "runner_identity")
    readonly_fields = ("token_hash", "bootstrap_payload")


class ExecutionOperationInline(admin.TabularInline):
    model = ExecutionOperation
    extra = 0


@admin.register(ExecutionOrder)
class ExecutionOrderAdmin(admin.ModelAdmin):
    list_display = ("tos_order_id", "site", "priority", "status", "released_at")
    list_filter = ("site", "status")
    search_fields = ("tos_order_id", "site__code", "site__enterprise__code")
    inlines = [ExecutionOperationInline]


@admin.register(EquipmentState)
class EquipmentStateAdmin(admin.ModelAdmin):
    list_display = (
        "name", "site", "equipment_type_code", "device_connected", "powered_on",
        "is_available", "is_dispatched", "last_seen_at",
    )
    list_filter = ("site", "equipment_type_code", "device_connected", "powered_on", "is_available")


admin.site.register(DispatchAssignment)
admin.site.register(EnterpriseProjection)
admin.site.register(SiteProjection)
admin.site.register(EquipmentCommand)
admin.site.register(InboxEvent)
admin.site.register(OutboxEvent)
admin.site.register(CommandResultReceipt)
