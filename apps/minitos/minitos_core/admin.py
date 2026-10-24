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

admin.site.register(ISOType)
admin.site.register(ContainerOwner)
admin.site.register(ContainerUnit)
admin.site.register(ContainerVisit)
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
admin.site.register(Order)
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
admin.site.register(CarrierVisit)
admin.site.register(POD)
admin.site.register(PointOfWork)
admin.site.register(ProcessHierarchy)
admin.site.register(BusinessProcess)
admin.site.register(ProcessVersion)
admin.site.register(BusinessProcessExecution)
