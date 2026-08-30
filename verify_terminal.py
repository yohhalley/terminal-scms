#!/usr/bin/env python
"""
Verify the populated test terminal data
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import Location, Block, PointOfWork, POWConnection, EquipmentType, Che, CarrierService, ServiceCargoRoute

location = Location.objects.get(name='Test Terminal')

print("\n" + "="*60)
print("TEST TERMINAL DATA VERIFICATION")
print("="*60)

print(f"\n📍 LOCATION: {location.name} ({location.location_type})")

print(f"\n📦 BLOCKS ({Block.objects.filter(location=location).count()}):")
for b in Block.objects.filter(location=location).order_by('name'):
    heap_str = "Transfer Zone" if b.is_heapzone else "Storage"
    print(f"   {b.name:12} {heap_str:15} ({b.max_bays}x{b.max_rows}x{b.max_tier})")

print(f"\n🎯 POINTS OF WORK ({PointOfWork.objects.filter(location=location).count()}):")
for p in PointOfWork.objects.filter(location=location).order_by('code'):
    print(f"   {p.code:15} {p.get_pow_type_display():25} ({p.latitude:.6f}, {p.longitude:.6f})")

print(f"\n🔗 POW CONNECTIONS: {POWConnection.objects.all().count()}")

print(f"\n🏗️  EQUIPMENT TYPES ({EquipmentType.objects.count()}):")
for eq_type in EquipmentType.objects.all():
    count = Che.objects.filter(equipment_type=eq_type, location=location).count()
    print(f"   {eq_type.name:5} - {count} units")

print(f"\n🚀 CHE UNITS ({Che.objects.filter(location=location).count()}):")
for c in Che.objects.filter(location=location).order_by('name'):
    pow_str = c.related_ches.first().code if c.related_ches.exists() else "N/A"
    print(f"   {c.name:8} ({c.equipment_type.name:5}) → POW: {pow_str}")

print(f"\n⛴️  CARRIER SERVICES ({CarrierService.objects.filter(location=location).count()}):")
for cs in CarrierService.objects.filter(location=location).order_by('name'):
    routes = ServiceCargoRoute.objects.filter(inbound_carrier_service=cs).count()
    print(f"   {cs.name:5} ({cs.carrier_service_type:10}) - {routes} inbound routes")

print(f"\n📋 SERVICE CARGO ROUTES: {ServiceCargoRoute.objects.count()}")

print("\n" + "="*60)
print("VERIFICATION COMPLETE")
print("="*60 + "\n")
