#!/usr/bin/env python3
"""
Test script to demonstrate PointOfWork and POWConnection models.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import Location, PointOfWork, POWConnection, Che
from datetime import datetime

def test_point_of_work():
    print("=== Testing PointOfWork and POWConnection Models ===\n")

    # Get existing location
    location = Location.objects.first()
    if not location:
        print("No location found. Please run populate_data.py first.")
        return

    print(f"Location: {location.name}")

    # Create some PointOfWork instances
    print("\n--- Creating PointOfWork instances ---")

    # Terminal area
    terminal_area, created = PointOfWork.objects.get_or_create(
        location=location,
        code='TA01',
        defaults={
            'name': 'Main Terminal Area',
            'pow_type': PointOfWork.TERMINAL_AREA,
            'x_position': 100.0,
            'y_position': 200.0,
            'max_throughput': 1000,
            'is_active': True
        }
    )
    print(f"Created terminal area: {terminal_area}")

    # Entry point
    entry_point, created = PointOfWork.objects.get_or_create(
        location=location,
        code='EP01',
        defaults={
            'name': 'Main Entry Gate',
            'pow_type': PointOfWork.ENTRY_POINT,
            'x_position': 50.0,
            'y_position': 150.0,
            'max_throughput': 200,
            'is_active': True
        }
    )
    print(f"Created entry point: {entry_point}")

    # Departure point
    departure_point, created = PointOfWork.objects.get_or_create(
        location=location,
        code='DP01',
        defaults={
            'name': 'Main Departure Gate',
            'pow_type': PointOfWork.DEPARTURE_POINT,
            'x_position': 150.0,
            'y_position': 150.0,
            'max_throughput': 200,
            'is_active': True
        }
    )
    print(f"Created departure point: {departure_point}")

    # Create POWConnection instances
    print("\n--- Creating POWConnection instances ---")

    # Entry to Terminal
    connection1, created = POWConnection.objects.get_or_create(
        from_pow=entry_point,
        to_pow=terminal_area,
        defaults={
            'distance': 500,
            'max_throughput': 150,
            'is_bidirectional': True,
            'cost': 1.0,
            'is_enabled': True
        }
    )
    print(f"Created connection: {connection1}")

    # Terminal to Departure
    connection2, created = POWConnection.objects.get_or_create(
        from_pow=terminal_area,
        to_pow=departure_point,
        defaults={
            'distance': 300,
            'max_throughput': 150,
            'is_bidirectional': True,
            'cost': 1.0,
            'is_enabled': True
        }
    )
    print(f"Created connection: {connection2}")

    # Direct Entry to Departure (for transshipment)
    connection3, created = POWConnection.objects.get_or_create(
        from_pow=entry_point,
        to_pow=departure_point,
        defaults={
            'distance': 800,
            'max_throughput': 50,
            'is_bidirectional': False,
            'cost': 2.5,  # Higher cost for longer path
            'is_enabled': True
        }
    )
    print(f"Created connection: {connection3}")

    # Show all PointOfWork instances
    print("\n--- All PointOfWork instances ---")
    pows = PointOfWork.objects.filter(location=location)
    for pow in pows:
        print(f"  {pow.code}: {pow.name} ({pow.get_pow_type_display()}) - Position: ({pow.x_position}, {pow.y_position})")

    # Show all POWConnection instances
    print("\n--- All POWConnection instances ---")
    connections = POWConnection.objects.all()
    for conn in connections:
        print(f"  {conn}")

    # Demonstrate graph traversal (simple example)
    print("\n--- Graph Traversal Example ---")
    print(f"Outgoing connections from {entry_point.code}:")
    for conn in entry_point.outgoing_connections.filter(is_enabled=True):
        print(f"  → {conn.to_pow.code} ({conn.distance}m, cost: {conn.cost})")

    print(f"\nIncoming connections to {terminal_area.code}:")
    for conn in terminal_area.incoming_connections.filter(is_enabled=True):
        print(f"  ← {conn.from_pow.code} ({conn.distance}m, cost: {conn.cost})")

    print("\n=== Test Complete ===")

if __name__ == '__main__':
    test_point_of_work()