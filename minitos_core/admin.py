from django.contrib import admin

from .models import (
    Area, Battery, Block, BusinessProcess, BusinessProcessExecution, CarrierService,
    CarrierVisit, Che, CheComponent, ComponentType, ContainerOwner, ContainerUnit,
    ContainerVisit, CurrentSignalValue, EnergyTank, Engine, EquipmentType,
    EquipmentTypeComponent, HandlingCycle, ISOType, Location, MotionAxis,
    OperationalStateInterval, Order, OrderLeg, ParkingArea, Pile, POD, Position,
    ProcessHierarchy, ProcessVersion, Road, RoutingNode, SignalBinding,
    SignalDefinition, Spreader, TerminalConnection, WorkQueue,
)

admin.site.register(ISOType)
admin.site.register(ContainerOwner)
admin.site.register(ContainerUnit)
admin.site.register(ContainerVisit)
admin.site.register(Location)
admin.site.register(Area)
admin.site.register(Block)
admin.site.register(Pile)
admin.site.register(Position)
admin.site.register(WorkQueue)
admin.site.register(Order)
admin.site.register(OrderLeg)
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
admin.site.register(Road)
admin.site.register(RoutingNode)
admin.site.register(ParkingArea)
admin.site.register(TerminalConnection)
admin.site.register(ProcessHierarchy)
admin.site.register(BusinessProcess)
admin.site.register(ProcessVersion)
admin.site.register(BusinessProcessExecution)
