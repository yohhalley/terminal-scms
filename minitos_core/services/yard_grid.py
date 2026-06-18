import math

from django.core.exceptions import ValidationError
from django.db import transaction

from minitos_core.models import Pile, Position


def _value(value):
    return float(value or 0)


def _pile_center(block, bay, row):
    heading = math.radians(_value(block.heading_deg))
    heading_axis = (math.sin(heading), math.cos(heading))
    right_axis = (math.cos(heading), -math.sin(heading))

    bay_start = block.padding_bottom_m if block.bay_use_heading_axis else block.padding_left_m
    row_start = block.padding_left_m if block.bay_use_heading_axis else block.padding_bottom_m
    bay_offset = _value(bay_start) + block.pile_length_m / 2
    bay_offset += (bay - 1) * (block.pile_length_m + block.bay_spacing_m)
    row_offset = _value(row_start) + block.pile_width_m / 2
    row_offset += (row - 1) * (block.pile_width_m + block.row_spacing_m)

    if block.bay_use_heading_axis:
        north_offset, east_offset = bay_offset, row_offset
    else:
        north_offset, east_offset = row_offset, bay_offset

    x = _value(block.x_position)
    y = _value(block.y_position)
    x += heading_axis[0] * north_offset + right_axis[0] * east_offset
    y += heading_axis[1] * north_offset + right_axis[1] * east_offset
    return x, y


def models_q_protected_position():
    from django.db.models import Q

    return Q(is_occupied=True) | Q(is_reserved=True) | Q(container_visit__isnull=False)


@transaction.atomic
def sync_yard_grid(block):
    """Create or resize a block grid while preserving operational position data."""
    existing = {
        (pile.bay, pile.row): pile
        for pile in Pile.objects.select_for_update().filter(block=block)
    }
    desired = {
        (bay, row)
        for bay in range(1, block.max_bays + 1)
        for row in range(1, block.max_rows + 1)
    }

    obsolete = [pile for key, pile in existing.items() if key not in desired]
    protected = Position.objects.filter(pile__in=obsolete).filter(models_q_protected_position())
    if protected.exists():
        raise ValidationError('The grid cannot be reduced because removed piles contain reserved or occupied positions.')

    for bay, row in sorted(desired):
        x, y = _pile_center(block, bay, row)
        pile = existing.get((bay, row))
        if pile is None:
            pile = Pile.objects.create(
                block=block,
                bay=bay,
                row=row,
                tiers=block.max_tier,
                x_position=x,
                y_position=y,
            )
        else:
            pile.tiers = block.max_tier
            pile.x_position = x
            pile.y_position = y
            pile.save(update_fields=['tiers', 'x_position', 'y_position'])

        extra_positions = pile.positions.filter(tier__gt=block.max_tier)
        if extra_positions.filter(models_q_protected_position()).exists():
            raise ValidationError(
                f'Pile {pile.get_pile_name()} cannot lose tiers that contain reserved or occupied positions.'
            )
        extra_positions.delete()
        existing_tiers = set(pile.positions.values_list('tier', flat=True))
        Position.objects.bulk_create([
            Position(pile=pile, tier=tier)
            for tier in range(1, block.max_tier + 1)
            if tier not in existing_tiers
        ])

    if obsolete:
        Pile.objects.filter(pk__in=[pile.pk for pile in obsolete]).delete()
    return block
