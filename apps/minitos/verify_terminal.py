#!/usr/bin/env python3
"""Print a compact summary of a terminal's current layout and routing graph."""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import (  # noqa: E402
    CarrierService, Che, EquipmentType, LayoutAsset, Location, PointOfWork,
    RouteEdge, ServiceCargoRoute, StorageGrid,
)
from minitos_core.services import current_layout  # noqa: E402


location = Location.objects.get(name='Test Terminal')
layout = current_layout(location)

print('=' * 60)
print(f'{location.name} - layout v{layout.version} ({layout.status})')
print('=' * 60)

grids = StorageGrid.objects.filter(layout_asset__layout_version=layout).select_related('layout_asset')
print(f'\nSTORAGE GRIDS ({grids.count()}):')
for grid in grids:
    print(f'  {grid.layout_asset.code:12} {grid.grid_kind:12} {grid.max_bays}x{grid.max_rows}x{grid.max_tier}')

assets = LayoutAsset.objects.filter(layout_version=layout)
print(f'\nLAYOUT ASSETS: {assets.count()}')
print(f'ROUTE EDGES: {RouteEdge.objects.filter(layout_version=layout).count()}')

print(f'\nPOINTS OF WORK ({PointOfWork.objects.filter(location=location).count()}):')
for point in PointOfWork.objects.filter(location=location).order_by('code'):
    node = point.route_node.code if point.route_node else 'none'
    print(f'  {point.code:15} {point.get_pow_type_display():25} node={node}')

print(f'\nEQUIPMENT TYPES ({EquipmentType.objects.count()}):')
for equipment_type in EquipmentType.objects.all():
    count = Che.objects.filter(equipment_type=equipment_type, location=location).count()
    print(f'  {equipment_type.name:24} {count} units')

print(f'\nCHE ({Che.objects.filter(location=location).count()}):')
for che in Che.objects.filter(location=location).select_related('current_asset').order_by('name'):
    asset = che.current_asset.code if che.current_asset else 'unassigned'
    print(f'  {che.name:12} {che.equipment_type.equipment_type:5} asset={asset}')

services = CarrierService.objects.filter(location=location)
print(f'\nCARRIER SERVICES: {services.count()}')
print(f'SERVICE CARGO ROUTES: {ServiceCargoRoute.objects.filter(inbound_carrier_service__location=location).count()}')
