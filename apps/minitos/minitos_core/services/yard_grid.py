import math

from django.core.exceptions import ValidationError
from django.db import transaction

from minitos_core.models import OperationalPosition, Pile, Position
from minitos_core.services.coordinates import offset_geographic


def _value(value):
    return float(value or 0)


def _pile_center(grid, bay, row):
    heading = math.radians(_value(grid.heading_deg))
    heading_axis = (math.sin(heading), math.cos(heading))
    right_axis = (math.cos(heading), -math.sin(heading))

    bay_start = grid.padding_bottom_m if grid.bay_use_heading_axis else grid.padding_left_m
    row_start = grid.padding_left_m if grid.bay_use_heading_axis else grid.padding_bottom_m
    bay_offset = _value(bay_start) + grid.pile_length_m / 2
    bay_offset += (bay - 1) * (grid.pile_length_m + grid.bay_spacing_m)
    row_offset = _value(row_start) + grid.pile_width_m / 2
    row_offset += (row - 1) * (grid.pile_width_m + grid.row_spacing_m)

    if grid.bay_use_heading_axis:
        north_offset, east_offset = bay_offset, row_offset
    else:
        north_offset, east_offset = row_offset, bay_offset

    if grid.latitude is None or grid.longitude is None:
        raise ValidationError('A yard block requires an anchor latitude and longitude.')
    east = heading_axis[0] * north_offset + right_axis[0] * east_offset
    north = heading_axis[1] * north_offset + right_axis[1] * east_offset
    return offset_geographic(grid.latitude, grid.longitude, east, north)


def models_q_protected_position():
    from django.db.models import Q

    return Q(is_occupied=True) | Q(is_reserved=True) | Q(container_visit__isnull=False)


@transaction.atomic
def _sync_operational_positions(grid, pile):
    layout_version = grid.layout_asset.layout_version
    for position in pile.positions.all():
        code = f'{grid.layout_asset.code}.{pile.get_bay_name()}.{pile.get_row_name()}.{position.tier:02d}'
        OperationalPosition.objects.update_or_create(
            stack_position=position,
            defaults={
                'layout_version': layout_version,
                'asset': grid.layout_asset,
                'code': code,
                'name': code,
                'position_kind': OperationalPosition.YARD_SLOT,
                'latitude': pile.latitude,
                'longitude': pile.longitude,
                'elevation_m': (position.tier - 1) * grid.pile_height_m,
                'logical_address': {
                    'block': grid.layout_asset.code,
                    'bay': pile.get_bay_name(),
                    'row': pile.get_row_name(),
                    'tier': position.tier,
                },
            },
        )


@transaction.atomic
def sync_yard_grid(grid):
    """Create or resize a block grid while preserving operational position data."""
    existing = {
        (pile.bay, pile.row): pile
        for pile in Pile.objects.select_for_update().filter(storage_grid=grid)
    }
    desired = {
        (bay, row)
        for bay in range(1, grid.max_bays + 1)
        for row in range(1, grid.max_rows + 1)
    }

    obsolete = [pile for key, pile in existing.items() if key not in desired]
    protected = Position.objects.filter(pile__in=obsolete).filter(models_q_protected_position())
    if protected.exists():
        raise ValidationError('The grid cannot be reduced because removed piles contain reserved or occupied positions.')

    for bay, row in sorted(desired):
        latitude, longitude = _pile_center(grid, bay, row)
        pile = existing.get((bay, row))
        if pile is None:
            pile = Pile.objects.create(
                storage_grid=grid,
                bay=bay,
                row=row,
                tiers=grid.max_tier,
                latitude=latitude,
                longitude=longitude,
            )
        else:
            pile.tiers = grid.max_tier
            pile.latitude = latitude
            pile.longitude = longitude
            pile.save(update_fields=['tiers', 'latitude', 'longitude'])

        extra_positions = pile.positions.filter(tier__gt=grid.max_tier)
        if extra_positions.filter(models_q_protected_position()).exists():
            raise ValidationError(
                f'Pile {pile.get_pile_name()} cannot lose tiers that contain reserved or occupied positions.'
            )
        extra_positions.delete()
        existing_tiers = set(pile.positions.values_list('tier', flat=True))
        Position.objects.bulk_create([
            Position(pile=pile, tier=tier)
            for tier in range(1, grid.max_tier + 1)
            if tier not in existing_tiers
        ])
        _sync_operational_positions(grid, pile)

    if obsolete:
        Pile.objects.filter(pk__in=[pile.pk for pile in obsolete]).delete()
    return grid
