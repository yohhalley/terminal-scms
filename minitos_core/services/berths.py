import math

from django.core.exceptions import ValidationError
from django.db import transaction

from minitos_core.models import Bollard
from minitos_core.services.coordinates import offset_geographic


def berth_polygon(latitude, longitude, heading_deg, length_m, width_m):
    """Return a rectangle growing along heading and to its right."""
    angle = math.radians(float(heading_deg))
    along_east = math.sin(angle)
    along_north = math.cos(angle)
    right_east = math.cos(angle)
    right_north = -math.sin(angle)

    def point(along_m, right_m):
        lat, lon = offset_geographic(
            latitude,
            longitude,
            along_east * along_m + right_east * right_m,
            along_north * along_m + right_north * right_m,
        )
        return [lon, lat]

    return [point(0, 0), point(length_m, 0), point(length_m, width_m), point(0, width_m)]


def normalize_bollard_spacings(berth, spacings=None):
    gap_count = berth.bollard_count - 1
    if spacings is None:
        spacings = [berth.default_bollard_spacing_m] * gap_count
    if len(spacings) != gap_count:
        raise ValidationError(f'Provide exactly {gap_count} bollard spacing values.')
    normalized = [float(value) for value in spacings]
    if any(value <= 0 for value in normalized):
        raise ValidationError('Bollard spacings must be greater than zero.')
    occupied_length = berth.start_margin_m + sum(normalized) + berth.end_margin_m
    if occupied_length > berth.length_m + 0.001:
        raise ValidationError('Bollard spacings and margins exceed the berth length.')
    return normalized


@transaction.atomic
def sync_berth_bollards(berth, spacings=None):
    if spacings is None and berth.pk and berth.bollards.count() == berth.bollard_count:
        spacings = list(
            berth.bollards.filter(number__gt=1)
            .order_by('number')
            .values_list('spacing_from_previous_m', flat=True)
        )
    spacings = normalize_bollard_spacings(berth, spacings)
    existing = {bollard.number: bollard for bollard in berth.bollards.all()}
    is_reverse = berth.bollard_direction == berth.BOLLARDS_REVERSE
    distance = (
        float(berth.length_m) - float(berth.end_margin_m)
        if is_reverse else float(berth.start_margin_m)
    )
    angle = math.radians(float(berth.heading_deg))
    along_east = math.sin(angle)
    along_north = math.cos(angle)
    right_east = math.cos(angle)
    right_north = -math.sin(angle)
    side_offset = float(berth.width_m) if berth.water_side == berth.WATER_RIGHT else 0.0

    for number in range(1, berth.bollard_count + 1):
        spacing = None if number == 1 else spacings[number - 2]
        if spacing is not None:
            distance += -spacing if is_reverse else spacing
        latitude, longitude = offset_geographic(
            berth.latitude,
            berth.longitude,
            along_east * distance + right_east * side_offset,
            along_north * distance + right_north * side_offset,
        )
        bollard = existing.pop(number, Bollard(berth=berth, number=number))
        bollard.name = str(number)
        bollard.distance_along_berth_m = distance
        bollard.spacing_from_previous_m = spacing
        bollard.latitude = latitude
        bollard.longitude = longitude
        bollard.save()

    if existing:
        Bollard.objects.filter(pk__in=[item.pk for item in existing.values()]).delete()
    return berth


def vessel_end_bollard(berth, start_bollard, vessel_length_m):
    """Find the first higher-numbered bollard covering the vessel length."""
    if start_bollard.berth_id != berth.id:
        raise ValidationError('The initial bollard must belong to the selected berth.')
    length = float(vessel_length_m)
    for bollard in berth.bollards.filter(number__gte=start_bollard.number).order_by('number'):
        if abs(bollard.distance_along_berth_m - start_bollard.distance_along_berth_m) >= length:
            return bollard
    raise ValidationError('The vessel does not fit between the initial bollard and the end of the berth.')
