#!/usr/bin/env python
"""
Data population script for creating a comprehensive test terminal with all models.
Run: python manage.py shell < populate_data.py
"""
import os
import django
from datetime import datetime, time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import (
    Location, CarrierService, ServiceCargoRoute, ISOType, POD, ContainerOwner,
    Block, PointOfWork, POWConnection, EquipmentType, Che
)

# Create or get location (Terminal)
location, created = Location.objects.get_or_create(
    name='Test Terminal',
    defaults={'location_type': 'TL'}
)
print(f"\n{'='*60}")
print(f"Location: {location.name} (Created: {created})")
print(f"{'='*60}")

# ========== CREATE BLOCKS AND TRANSFER ZONES ==========
print(f"\n{'='*60}")
print(f"Creating Yard Blocks and Transfer Zones")
print(f"{'='*60}")

# Define block layout: (name, x_position, y_position)
block_layout = [
    ('A01', 100.0, 100.0),
    ('B01', 400.0, 100.0),
    ('A02', 100.0, 200.0),
    ('B02', 400.0, 200.0),
    ('A03', 100.0, 300.0),
    ('B03', 400.0, 300.0),
]

blocks = {}
transfer_zones = {}

for block_name, x_pos, y_pos in block_layout:
    # Create storage block
    block, created = Block.objects.get_or_create(
        location=location,
        name=block_name,
        defaults={
            'block_type': 'AB',  # Automated Block
            'max_bays': 25,
            'max_rows': 8,
            'max_tier': 5,
            'x_position': x_pos,
            'y_position': y_pos,
            'is_heapzone': False
        }
    )
    blocks[block_name] = block
    print(f"Block {block_name}: {block.max_bays} bays × {block.max_rows} rows × {block.max_tier} tiers (Pos: {x_pos}, {y_pos})")

    # Create transfer zone (TZ) adjacent to block
    tz_name = f"TZ-{block_name}"
    tz_x_pos = x_pos + 50.0  # Adjacent to the block
    tz, created = Block.objects.get_or_create(
        location=location,
        name=tz_name,
        defaults={
            'block_type': 'TZ',  # Transfer Zone
            'max_bays': 25,  # Same number of bays as the block
            'max_rows': 1,   # Only 1 row
            'max_tier': 1,   # Only 1 tier
            'x_position': tz_x_pos,
            'y_position': y_pos,
            'is_heapzone': False
        }
    )
    transfer_zones[tz_name] = tz
    print(f"  → {tz_name}: {tz.max_bays} bays × {tz.max_rows} row × {tz.max_tier} tier (Pos: {tz_x_pos}, {y_pos})")

# ========== CREATE POINTS OF WORK ==========
print(f"\n{'='*60}")
print(f"Creating Points of Work")
print(f"{'='*60}")

pows = {}

# Create POWs for each block (one POW covers block + its TZ)
for block_name, _, _ in block_layout:
    pow_name = f"POW-{block_name}"
    pow, created = PointOfWork.objects.get_or_create(
        location=location,
        code=pow_name,
        defaults={
            'name': pow_name,
            'pow_type': PointOfWork.TERMINAL_AREA,
            'x_position': blocks[block_name].x_position,
            'y_position': blocks[block_name].y_position,
            'max_throughput': 500,
            'is_active': True
        }
    )
    pows[pow_name] = pow
    print(f"POW {pow_name}: Terminal Area (Pos: {pow.x_position}, {pow.y_position})")

# Create STS POWs
sts_pow_names = ['POW-STS01', 'POW-STS02']
for i, sts_pow_name in enumerate(sts_pow_names, 1):
    pow, created = PointOfWork.objects.get_or_create(
        location=location,
        code=sts_pow_name,
        defaults={
            'name': sts_pow_name,
            'pow_type': PointOfWork.ENTRY_DEPARTURE_POINT,
            'x_position': 250.0 + (i * 100),
            'y_position': 50.0,
            'max_throughput': 300,
            'is_active': True
        }
    )
    pows[sts_pow_name] = pow
    print(f"POW {sts_pow_name}: Entry & Departure Point (STS Crane)")

# Create Gate POWs
gate_pow_in, created = PointOfWork.objects.get_or_create(
    location=location,
    code='POW-Gate-In',
    defaults={
        'name': 'POW-Gate-In',
        'pow_type': PointOfWork.ENTRY_POINT,
        'x_position': 0.0,
        'y_position': 150.0,
        'max_throughput': 200,
        'is_active': True
    }
)
pows['POW-Gate-In'] = gate_pow_in
print(f"POW POW-Gate-In: Entry Point")

gate_pow_out, created = PointOfWork.objects.get_or_create(
    location=location,
    code='POW-Gate-Out',
    defaults={
        'name': 'POW-Gate-Out',
        'pow_type': PointOfWork.DEPARTURE_POINT,
        'x_position': 600.0,
        'y_position': 150.0,
        'max_throughput': 200,
        'is_active': True
    }
)
pows['POW-Gate-Out'] = gate_pow_out
print(f"POW POW-Gate-Out: Departure Point")

# ========== CREATE POW CONNECTIONS ==========
print(f"\n{'='*60}")
print(f"Creating POW Connections")
print(f"{'='*60}")

connections_created = 0

def create_connection(from_pow_name, to_pow_name, distance=500):
    global connections_created
    from_pow = pows[from_pow_name]
    to_pow = pows[to_pow_name]
    
    # Check if connection already exists (avoid duplicates)
    conn, created = POWConnection.objects.get_or_create(
        from_pow=from_pow,
        to_pow=to_pow,
        defaults={
            'distance': distance,
            'max_throughput': 150,
            'is_bidirectional': True,
            'cost': 1.0,
            'is_enabled': True
        }
    )
    if created:
        connections_created += 1
        return True
    return False

# Connect each block POW to its transfer zone
for block_name, _, _ in block_layout:
    tz_name = f"TZ-{block_name}"
    pow_name = f"POW-{block_name}"
    
    # They share the same POW, so no connection needed
    # Instead, let's connect the block's POW to its TZ's POW conceptually
    # But since we only have one POW per block+TZ pair, we skip this
    pass

# Connect all transfer zones to each other
block_pows = [f"POW-{name}" for name, _, _ in block_layout]

for i, pow1_name in enumerate(block_pows):
    for pow2_name in block_pows[i+1:]:
        if create_connection(pow1_name, pow2_name, distance=300):
            print(f"  {pow1_name} ↔ {pow2_name}")

# Connect all transfer zones to both STS POWs
for pow_name in block_pows:
    for sts_pow_name in sts_pow_names:
        if create_connection(pow_name, sts_pow_name, distance=400):
            print(f"  {pow_name} ↔ {sts_pow_name}")

# Connect all transfer zones to both Gate POWs
for pow_name in block_pows:
    if create_connection(pow_name, 'POW-Gate-In', distance=500):
        print(f"  {pow_name} ↔ POW-Gate-In")
    if create_connection(pow_name, 'POW-Gate-Out', distance=500):
        print(f"  {pow_name} ↔ POW-Gate-Out")

# Connect STS POWs to each other
if create_connection(sts_pow_names[0], sts_pow_names[1], distance=200):
    print(f"  {sts_pow_names[0]} ↔ {sts_pow_names[1]}")

# Connect Gate POWs to each other
if create_connection('POW-Gate-In', 'POW-Gate-Out', distance=600):
    print(f"  POW-Gate-In ↔ POW-Gate-Out")

print(f"Total POW Connections created: {connections_created}")

# ========== CREATE EQUIPMENT TYPES ==========
print(f"\n{'='*60}")
print(f"Creating Equipment Types")
print(f"{'='*60}")

equipment_types = {}

sts_type, created = EquipmentType.objects.get_or_create(
    name='Ship-to-Shore',
    defaults={
        'equipment_type': 'STS',
        'running_hours': 0
    }
)
equipment_types['STS'] = sts_type
print(f"Equipment Type: STS (Ship-to-Shore Crane)")

rtg_type, created = EquipmentType.objects.get_or_create(
    name='Rubber Tyred Gantry',
    defaults={
        'equipment_type': 'RTG',
        'running_hours': 0
    }
)
equipment_types['RTG'] = rtg_type
print(f"Equipment Type: RTG (Rubber Tyred Gantry)")

tt_type, created = EquipmentType.objects.get_or_create(
    name='Terminal Truck',
    defaults={
        'equipment_type': 'TT',
        'running_hours': 0
    }
)
equipment_types['TT'] = tt_type
print(f"Equipment Type: TT (Terminal Truck)")

# ========== CREATE EQUIPMENT (CHEs) ==========
print(f"\n{'='*60}")
print(f"Creating Equipment")
print(f"{'='*60}")

# Create STS equipment
sts_list = []
for i in range(1, 3):  # 2 STS
    sts_name = f"STS{i:02d}"
    sts_che, created = Che.objects.get_or_create(
        name=sts_name,
        defaults={
            'equipment_type': equipment_types['STS'],
            'status': 'IL',  # Idle
            'is_available': True,
            'location': location,
            'x_position': 250.0 + (i * 100),
            'y_position': 50.0,
            'fuel_level': 100,
            'current_block': None
        }
    )
    sts_list.append(sts_che)
    # Connect to corresponding STS POW
    sts_pow = pows[f"POW-STS{i:02d}"]
    sts_che.related_ches.add(sts_pow) if hasattr(sts_che, 'related_ches') else None
    print(f"CHE STS{i:02d}: Connected to POW-STS{i:02d}")

# Create RTG equipment (one per block)
rtg_list = []
for block_idx, (block_name, _, _) in enumerate(block_layout, 1):
    rtg_name = f"RTG{block_idx:02d}"
    rtg_che, created = Che.objects.get_or_create(
        name=rtg_name,
        defaults={
            'equipment_type': equipment_types['RTG'],
            'status': 'IL',  # Idle
            'is_available': True,
            'location': location,
            'x_position': blocks[block_name].x_position,
            'y_position': blocks[block_name].y_position,
            'fuel_level': 100,
            'current_block': blocks[block_name]
        }
    )
    rtg_list.append(rtg_che)
    pow_name = f"POW-{block_name}"
    # Note: We'd connect to POW via related_ches if the field existed
    # For now, just track the block assignment
    print(f"CHE {rtg_name}: Assigned to Block {block_name} (POW-{block_name})")

# Create Terminal Truck equipment
tt_list = []
for i in range(1, 6):  # 5 TT
    tt_name = f"TT{i:02d}"
    tt_che, created = Che.objects.get_or_create(
        name=tt_name,
        defaults={
            'equipment_type': equipment_types['TT'],
            'status': 'IL',  # Idle
            'is_available': True,
            'location': location,
            'x_position': 200.0 + (i * 50),
            'y_position': 400.0,
            'fuel_level': 100,
            'current_block': None
        }
    )
    tt_list.append(tt_che)
    print(f"CHE TT{i:02d}: General purpose terminal truck")

# Create ISOTypes if they don't exist
print(f"\n{'='*60}")
print(f"Creating ISO Types")
print(f"{'='*60}")

iso_types_data = [
    {'iso_type': '22G1', 'length': '20', 'height': 'NC'},
    {'iso_type': '42G1', 'length': '40', 'height': 'NC'},
    {'iso_type': '45G1', 'length': '45', 'height': 'NC'},
]

iso_types = {}
for iso_data in iso_types_data:
    iso_type, created = ISOType.objects.get_or_create(**iso_data)
    iso_types[iso_data['iso_type']] = iso_type
    print(f"ISOType: {iso_type.iso_type} (Created: {created})")

# Create PODs if they don't exist
print(f"\n{'='*60}")
print(f"Creating Ports of Discharge")
print(f"{'='*60}")

pods_data = [
    {'name': 'Terminal', 'uncode': 'TV', 'country': 'Internal'},
    {'name': 'POD2', 'uncode': 'POD2', 'country': 'Port2'},
    {'name': 'POD3', 'uncode': 'POD3', 'country': 'Port3'},
    {'name': 'POD4', 'uncode': 'POD4', 'country': 'Port4'},
    {'name': 'POD5', 'uncode': 'POD5', 'country': 'Port5'},
]

pods = {}
for pod_data in pods_data:
    pod, created = POD.objects.get_or_create(**pod_data)
    pods[pod_data['uncode']] = pod
    print(f"POD: {pod.name} ({pod.uncode}) (Created: {created})")

# Create CarrierServices
carrier_services_data = [
    {
        'name': 'VS1',
        'carrier_service_type': 'Vessel',
        'repeat': 'Weekly',
        'repeat_weekly': 1,
        'on_weekdays': 'Monday',
        'window_start_time': time(8, 0),  # 8:00 AM
        'window_end_time': time(14, 0),   # 2:00 PM next day (converted to 14 hours = 840 min)
        'duration': 1800,  # 30 hours in minutes
        'start_date': datetime.now().date(),
    },
    {
        'name': 'VS2',
        'carrier_service_type': 'Vessel',
        'repeat': 'Weekly',
        'repeat_weekly': 1,
        'on_weekdays': 'Wednesday',
        'window_start_time': time(22, 0),  # 10:00 PM
        'window_end_time': time(4, 0),     # 4:00 AM next day
        'duration': 1800,  # 30 hours
        'start_date': datetime.now().date(),
    },
    {
        'name': 'VS3',
        'carrier_service_type': 'Vessel',
        'repeat': 'Weekly',
        'repeat_weekly': 1,
        'on_weekdays': 'Friday',
        'window_start_time': time(4, 0),   # 4:00 AM
        'window_end_time': time(10, 0),    # 10:00 AM
        'duration': 1800,  # 30 hours
        'start_date': datetime.now().date(),
    },
    {
        'name': 'GS1',
        'carrier_service_type': 'Truck',
        'repeat': 'Weekly',
        'repeat_weekly': 1,
        'on_weekdays': 'Monday,Tuesday,Wednesday,Thursday,Friday,Saturday',  # Daily except Sunday
        'window_start_time': time(6, 0),   # 6:00 AM
        'window_end_time': time(20, 0),    # 8:00 PM
        'duration': 840,  # 14 hours in minutes
        'start_date': datetime.now().date(),
    },
]

carrier_services = {}
for cs_data in carrier_services_data:
    cs, created = CarrierService.objects.get_or_create(
        name=cs_data['name'],
        defaults={**cs_data, 'location': location}
    )
    carrier_services[cs_data['name']] = cs
    print(f"CarrierService: {cs.name} (Created: {created})")

# Create ServiceCargoRoutes
cargo_routes_data = [
    # VS1 routes
    ('VS1', 'GS1', 'TV', 'FCL', '22G1', None, False, 500, 5.4),
    ('VS1', 'GS1', 'TV', 'MTY', '22G1', None, False, 1000, 7),
    ('VS1', 'VS2', 'POD2', 'FCL', '22G1', None, False, 200, 3),
    ('VS1', 'VS1', None, 'FCL', '22G1', None, False, 100, 0.2),
    
    # VS3 to VS1 route
    ('VS3', 'VS1', 'POD3', 'FCL', '22G1', None, False, 200, 2),
    
    # GS1 routes
    ('GS1', 'VS1', 'TV', 'FCL', '45G1', None, False, 300, 4),
    ('GS1', 'VS1', 'TV', 'MTY', '45G1', None, False, 300, 7),
    
    # VS2 routes
    ('VS2', 'GS1', 'TV', 'FCL', '22G1', None, False, 800, 5.4),
    ('VS2', 'GS1', 'TV', 'MTY', '42G1', None, False, 300, 7),
    ('VS2', 'VS2', 'POD4', 'FCL', '42G1', None, False, 200, 3),
    ('VS2', 'VS2', None, 'FCL', '42G1', None, False, 30, 0.2),
    
    # GS2 routes (note: GS2 doesn't exist yet, we'll only create GS1 routes here)
    # Skip GS2 routes as GS2 is not defined
    
    # VS3 routes
    ('VS3', 'GS1', 'TV', 'FCL', '22G1', None, False, 1000, 5.4),
    ('VS3', 'GS1', 'TV', 'MTY', '22G1', None, False, 1500, 7),
    ('VS3', 'VS1', 'POD5', 'FCL', '42G1', None, False, 250, 3),
    ('VS3', 'VS3', None, 'FCL', '22G1', None, False, 50, 0.2),
]

for (ib_name, ob_name, pod_code, container_type, iso_code, hazard_codes, is_reefer, container_count, dwell_time) in cargo_routes_data:
    ib_service = carrier_services.get(ib_name)
    ob_service = carrier_services.get(ob_name)
    pod = pods.get(pod_code) if pod_code else None
    iso_type = iso_types.get(iso_code)
    
    if not ib_service or not ob_service or not iso_type:
        print(f"Skipping route {ib_name} → {ob_name}: Missing service or ISO type")
        continue
    
    try:
        scr, created = ServiceCargoRoute.objects.get_or_create(
            inbound_carrier_service=ib_service,
            outbound_carrier_service=ob_service,
            pod=pod,
            container_type=container_type,
            iso_type=iso_type,
            defaults={
                'hazard_codes': hazard_codes,
                'is_reefer': is_reefer,
                'container_count': container_count,
                'expected_dwell_time_days': dwell_time,
            }
        )
        print(f"ServiceCargoRoute: {ib_name} → {ob_name} ({container_type}) (Created: {created})")
    except Exception as e:
        print(f"Error creating route {ib_name} → {ob_name}: {e}")

print("\nData population complete!")
