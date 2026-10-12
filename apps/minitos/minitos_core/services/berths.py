import math

from django.core.exceptions import ValidationError
from django.db import transaction

from minitos_core.models import LinearMarker
from minitos_core.services.coordinates import offset_geographic
from minitos_core.services.layout import sync_axis_markers


def berth_polygon(latitude, longitude, heading_deg, length_m, width_m):
    """Return a quay rectangle growing along heading and to its right."""
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


@transaction.atomic
def sync_berth_bollards(
    berth,
    spacings=None,
    bollard_count=None,
    default_bollard_spacing_m=None,
    start_margin_m=None,
    end_margin_m=None,
):
    """Materialize quay bollards as markers on the berth's quay-face axis."""
    axis = berth.quay_face_axis
    settings = dict(axis.metadata.get('bollards', {}))
    settings.update({
        key: value for key, value in {
            'count': bollard_count,
            'default_spacing_m': default_bollard_spacing_m,
            'start_margin_m': start_margin_m,
            'end_margin_m': end_margin_m,
            'spacings': spacings,
        }.items() if value is not None
    })
    count = int(settings.get('count', 15))
    default_spacing = float(settings.get('default_spacing_m', 20.0))
    start_margin = float(settings.get('start_margin_m', 0.0))
    end_margin = float(settings.get('end_margin_m', 0.0))
    marker_spacings = settings.get('spacings')
    lateral_offset = float(settings.get('lateral_offset_m', 0.0))
    markers = sync_axis_markers(
        axis,
        LinearMarker.BOLLARD,
        count,
        default_spacing,
        start_margin,
        end_margin,
        marker_spacings,
        berth.bollard_direction == berth.BOLLARDS_REVERSE,
        lateral_offset,
    )
    axis.metadata = {
        **axis.metadata,
        'bollards': {
            'count': count,
            'default_spacing_m': default_spacing,
            'start_margin_m': start_margin,
            'end_margin_m': end_margin,
            'spacings': [marker.metadata.get('spacing_from_previous_m') for marker in markers[1:]],
            'lateral_offset_m': lateral_offset,
        },
    }
    axis.save(update_fields=['metadata'])
    return berth


def vessel_end_bollard(berth, start_bollard, vessel_length_m):
    if start_bollard.axis_id != berth.quay_face_axis_id:
        raise ValidationError('The initial bollard must belong to the selected berth segment.')
    low = min(berth.start_chainage_m, berth.end_chainage_m)
    high = max(berth.start_chainage_m, berth.end_chainage_m)
    if not low <= start_bollard.chainage_m <= high:
        raise ValidationError('The initial bollard is outside the selected berth segment.')
    length = float(vessel_length_m)
    direction = -1 if berth.bollard_direction == berth.BOLLARDS_REVERSE else 1
    candidates = list(berth.bollards)
    candidates.sort(key=lambda marker: marker.chainage_m, reverse=direction < 0)
    for marker in candidates:
        travel = direction * (marker.chainage_m - start_bollard.chainage_m)
        if travel >= length:
            return marker
    raise ValidationError('The vessel does not fit between the initial bollard and the end of the berth segment.')
