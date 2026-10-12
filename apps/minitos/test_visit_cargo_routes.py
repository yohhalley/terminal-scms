#!/usr/bin/env python3
"""
Test script to demonstrate VisitCargoRoute creation when CarrierVisit is created.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import (
    Location, CarrierService, ServiceCargoRoute,
    CarrierVisit, VisitCargoRoute, ISOType, POD
)
from datetime import datetime, date

def test_visit_cargo_route_creation():
    print("=== Testing VisitCargoRoute Creation ===\n")

    # Get existing data
    location = Location.objects.first()
    if not location:
        print("No location found. Please run populate_data.py first.")
        return

    print(f"Location: {location.name}")

    # Get VS1 service
    try:
        vs1_service = CarrierService.objects.get(name='VS1')
        print(f"Found VS1 service: {vs1_service.name}")
    except CarrierService.DoesNotExist:
        print("VS1 service not found. Please run populate_data.py first.")
        return

    # Check existing ServiceCargoRoutes for VS1
    service_routes = ServiceCargoRoute.objects.filter(inbound_carrier_service=vs1_service)
    print(f"ServiceCargoRoutes for VS1: {service_routes.count()}")

    for route in service_routes:
        print(f"  - {route.inbound_carrier_service.name} → {route.outbound_carrier_service.name} ({route.container_type}): {route.container_count} containers")

    # Check existing visits
    existing_visits = CarrierVisit.objects.filter(carrier_service=vs1_service)
    print(f"\nExisting visits for VS1: {existing_visits.count()}")

    # Delete any existing VV1 visit to ensure the signal triggers
    CarrierVisit.objects.filter(name='VV1').delete()
    print("\n--- Creating new CarrierVisit for VS1 ---")
    new_visit = CarrierVisit.objects.create(
        name='VV1',
        visit_type='Vessel',
        status='Created',
        location=location,
        carrier_service=vs1_service,
        eta=datetime(2024, 3, 15, 8, 0),  # March 15, 2024, 08:00
        etd=datetime(2024, 3, 15, 14, 0), # March 15, 2024, 14:00
        vessel_name='Test Vessel 1',
        voyage_number='TV001'
    )
    print(f"Created visit: {new_visit.name} (ID: {new_visit.id})")

    # Check if VisitCargoRoutes were created
    visit_routes = VisitCargoRoute.objects.filter(inbound_carrier_visit=new_visit)
    print(f"\nVisitCargoRoutes created for VV1: {visit_routes.count()}")

    for route in visit_routes:
        print(f"  - {route.inbound_carrier_visit.name} → {route.outbound_carrier_service.name} ({route.container_type}): {route.container_count} containers, {route.expected_dwell_time_days} days")

    print("\n=== Test Complete ===")

if __name__ == '__main__':
    test_visit_cargo_route_creation()