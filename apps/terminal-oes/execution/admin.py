from django.contrib import admin

from .models import (
    DispatchAssignment,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    InboxEvent,
    OutboxEvent,
)


class ExecutionOperationInline(admin.TabularInline):
    model = ExecutionOperation
    extra = 0


@admin.register(ExecutionOrder)
class ExecutionOrderAdmin(admin.ModelAdmin):
    list_display = ("tos_order_id", "terminal_code", "priority", "status", "released_at")
    list_filter = ("terminal_code", "status")
    search_fields = ("tos_order_id", "terminal_code")
    inlines = [ExecutionOperationInline]


@admin.register(EquipmentState)
class EquipmentStateAdmin(admin.ModelAdmin):
    list_display = (
        "name", "terminal_code", "equipment_type_code", "powered_on",
        "is_available", "is_dispatched", "last_seen_at",
    )
    list_filter = ("terminal_code", "equipment_type_code", "powered_on", "is_available")


admin.site.register(DispatchAssignment)
admin.site.register(EquipmentCommand)
admin.site.register(InboxEvent)
admin.site.register(OutboxEvent)
