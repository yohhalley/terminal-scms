import os
import django
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import Location, CarrierVisit

# Create a location
location = Location.objects.create(
    name='Test Terminal',
    location_type='TL',
    latitude=0.0,
    longitude=0.0
)
print(f'Created location: {location}')

# Create carrier visits
ib_carrier = CarrierVisit.objects.create(
    name='Vessel ABC',
    visit_type='Vessel',
    status='Arrived',
    location=location,
    eta=datetime.now(),
    etd=datetime.now()
)
print(f'Created IB carrier: {ib_carrier}')

ob_carrier = CarrierVisit.objects.create(
    name='Vessel XYZ',
    visit_type='Vessel',
    status='Planned',
    location=location,
    eta=datetime.now(),
    etd=datetime.now()
)
print(f'Created OB carrier: {ob_carrier}')

print('Test data created successfully!')