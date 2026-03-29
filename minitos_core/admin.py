from django.contrib import admin

from .models import ISOType, ContainerOwner, ContainerUnit, ContainerVisit, Location, Block, Pile, Position, WorkQueue, Order, OrderLeg, Che, EquipmentType, EquipmentPart, Route, CarrierVisit, POD

admin.site.register(ISOType)
admin.site.register(ContainerOwner)
admin.site.register(ContainerUnit)
admin.site.register(ContainerVisit)
admin.site.register(Location)
admin.site.register(Block)
admin.site.register(Pile)
admin.site.register(Position)
admin.site.register(WorkQueue)
admin.site.register(Order)
admin.site.register(OrderLeg)
admin.site.register(Che)
admin.site.register(EquipmentType)
admin.site.register(EquipmentPart)
admin.site.register(Route)
admin.site.register(CarrierVisit)
admin.site.register(POD)