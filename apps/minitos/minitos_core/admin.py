from django.contrib import admin

from .models import (
    AccessPolicy, Battery, BerthSegment, BusinessProcess,
    BusinessProcessExecution, CarrierService, CarrierVisit, Che, CheComponent,
    CheOperatingScope, ComponentType, ContainerOwner, ContainerUnit,
    ContainerVisit, CurrentSignalValue, EnergyTank, Engine, Enterprise, EquipmentType,
    EquipmentTypeComponent, HandlingCycle, IntegrationInbox, IntegrationOutbox,
    InterfacePosition, ISOType,
    LayoutAsset, LayoutPreset, LayoutVersion, LinearAxis, LinearMarker, Location,
    MotionAxis, OperationalPosition, OperationalStateInterval, Order, OrderLeg,
    OrderLegRouteEdge, OrderRoutePlan, Pile, POD, PointOfWork, Position,
    ProcessHierarchy, ProcessVersion, RouteEdge, RouteEdgeRequirement, RouteNode, Site,
    SiteConfigurationSnapshot,
    SignalBinding, SignalDefinition, Spreader, StorageGrid, TransferInterface,
    WorkQueue,
)
from . import demo_display
from .services.vessel_slots import vessel_slot_for, vessel_slots_for

admin.site.register(ISOType)
admin.site.register(ContainerOwner)
admin.site.register(ContainerUnit)
admin.site.register(Location)
admin.site.register(Enterprise)
admin.site.register(Site)
admin.site.register(SiteConfigurationSnapshot)
admin.site.register(LayoutVersion)
admin.site.register(LayoutAsset)
admin.site.register(StorageGrid)
admin.site.register(Pile)
admin.site.register(Position)
admin.site.register(AccessPolicy)
admin.site.register(LinearAxis)
admin.site.register(LinearMarker)
admin.site.register(BerthSegment)
admin.site.register(TransferInterface)
admin.site.register(InterfacePosition)
admin.site.register(OperationalPosition)
admin.site.register(CheOperatingScope)
admin.site.register(RouteNode)
admin.site.register(RouteEdge)
admin.site.register(RouteEdgeRequirement)
admin.site.register(LayoutPreset)
admin.site.register(WorkQueue)
admin.site.register(OrderLeg)
admin.site.register(OrderRoutePlan)
admin.site.register(OrderLegRouteEdge)
admin.site.register(IntegrationInbox)
admin.site.register(IntegrationOutbox)
admin.site.register(Che)
admin.site.register(EquipmentType)
admin.site.register(ComponentType)
admin.site.register(EquipmentTypeComponent)
admin.site.register(CheComponent)
admin.site.register(Engine)
admin.site.register(Battery)
admin.site.register(EnergyTank)
admin.site.register(MotionAxis)
admin.site.register(Spreader)
admin.site.register(SignalDefinition)
admin.site.register(SignalBinding)
admin.site.register(CurrentSignalValue)
admin.site.register(OperationalStateInterval)
admin.site.register(HandlingCycle)
admin.site.register(CarrierService)
admin.site.register(POD)
admin.site.register(PointOfWork)
admin.site.register(ProcessHierarchy)
admin.site.register(BusinessProcess)
admin.site.register(ProcessVersion)
admin.site.register(BusinessProcessExecution)


# Demo screens S3 (D-041, D-031; TCL-demo-screens): the spec's step-1 UI is
# the admin changelist, so ContainerVisit and Order show the ISO container
# number and the vessel slot (services/vessel_slots.py, BBBRRTT via
# logical_address.container_visit_id), and CarrierVisit shows when the
# discharge list was received (terminal local time, zone named). Formats and
# the missing value are the demo_display placeholders.
class VesselSlotColumnMixin:
    """Look up the vessel slots of a changelist page in two queries (current layouts, slots)."""

    def vessel_slot_container_visit(self, obj):  # the ContainerVisit whose slot to show
        raise NotImplementedError

    def get_changelist_instance(self, request):
        changelist = super().get_changelist_instance(request)
        objects = list(changelist.result_list)
        visits = [self.vessel_slot_container_visit(obj) for obj in objects]
        slots = vessel_slots_for(visits)
        for obj, visit in zip(objects, visits):
            obj._demo_vessel_slot = slots.get(visit.pk) if visit is not None else None
        return changelist

    @admin.display(description='Vessel slot')
    def vessel_slot(self, obj):
        if hasattr(obj, '_demo_vessel_slot'):
            stow = obj._demo_vessel_slot
        else:
            stow = vessel_slot_for(self.vessel_slot_container_visit(obj))
        return demo_display.format_vessel_slot(stow)


@admin.register(ContainerVisit)
class ContainerVisitAdmin(VesselSlotColumnMixin, admin.ModelAdmin):
    # status_display (not the raw field) so the changelist uses the D-032
    # label without changing STATUS_CHOICES or adding a migration.
    list_display = ('__str__', 'container_number_display', 'vessel_slot', 'status_display', 'category', 'ib_carrier')
    list_select_related = ('container_number', 'ib_carrier')

    def vessel_slot_container_visit(self, obj):
        return obj

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return obj.get_status_display()

    @admin.display(description='Container number', ordering='container_number')
    def container_number_display(self, obj):
        return demo_display.format_container_number(obj.container_number_id)


@admin.register(Order)
class OrderAdmin(VesselSlotColumnMixin, admin.ModelAdmin):
    list_display = ('__str__', 'order_type', 'status', 'container_number_display', 'vessel_slot', 'work_queue')
    list_select_related = ('container_number__container_number', 'container_number__ib_carrier', 'work_queue')

    def vessel_slot_container_visit(self, obj):
        return obj.container_number

    @admin.display(description='Container number', ordering='container_number__container_number')
    def container_number_display(self, obj):
        visit = obj.container_number
        return demo_display.format_container_number(visit.container_number_id if visit else None)


@admin.register(CarrierVisit)
class CarrierVisitAdmin(admin.ModelAdmin):
    # QA review of #54, S2: ata goes through the same helper and display zone
    # as the received time, so one row never mixes UTC and terminal time.
    list_display = ('name', 'visit_type', 'status', 'vessel_name', 'ata_display', 'discharge_list_received')
    list_select_related = ('location',)

    @admin.display(description='Berthed (ATA)', ordering='ata')
    def ata_display(self, obj):
        return demo_display.format_local_time(obj.ata, obj.location, with_zone=True)

    @admin.display(description='Discharge list received', ordering='discharge_list_received_at')
    def discharge_list_received(self, obj):
        return demo_display.format_local_time(obj.discharge_list_received_at, obj.location, with_zone=True)
