"""Logical rail envelopes shared by editor placement and simulator actions."""

import math

from django.core.exceptions import ValidationError

from minitos_core.models import BerthSegment, CheOperatingScope, LinearAxis
from .layout import geographic_distance_m, nearest_axis_chainage, point_along_axis, polyline_length_m

FIXED_CODES = {'STS', 'QC', 'RMG', 'ARMG', 'ASC', 'RGC', 'ARTG'}


def rail_mode(code):
    return 'fixed' if code in FIXED_CODES else 'repositionable' if code == 'RTG' else 'free'


def rail_axis_kinds(code):
    kinds = [LinearAxis.CRANE_RAIL]
    if code in {'STS', 'QC'}:
        kinds.append(LinearAxis.QUAY_FACE)
    elif rail_mode(code) == 'fixed':
        kinds.append(LinearAxis.RAIL_TRACK)
    return kinds


def project_rail(points, coordinate):
    chainage = nearest_axis_chainage(points, coordinate)
    point = point_along_axis(points, chainage)
    cumulative = 0.0
    start, end = points[-2:]
    for first, last in zip(points, points[1:]):
        cumulative += geographic_distance_m(first, last)
        if chainage <= cumulative:
            start, end = first, last
            break
    east = (end[0] - start[0]) * math.cos(math.radians((start[1] + end[1]) / 2))
    north = end[1] - start[1]
    return {'point': point, 'chainage_m': chainage,
            'heading_deg': math.degrees(math.atan2(east, north)) % 360,
            'distance_m': geographic_distance_m(coordinate, point)}


def _clip_axis(points, minimum, maximum):
    length = polyline_length_m(points)
    low = max(0.0, float(minimum or 0))
    high = min(length, float(maximum) if maximum is not None else length)
    if low >= high:
        return []
    result = [point_along_axis(points, low)]
    cumulative = 0.0
    for first, last in zip(points, points[1:]):
        cumulative += geographic_distance_m(first, last)
        if low < cumulative < high:
            result.append(list(last))
    result.append(point_along_axis(points, high))
    return result


def _sts_facing(axis, points, coordinate):
    """Return the offset from rail tangent that makes an STS face the water."""
    quay = axis.asset
    while quay.parent_id and quay.asset_kind != 'quay':
        quay = quay.parent
    segments = list(BerthSegment.objects.filter(
        quay_asset_id=quay.pk, is_active=True,
    ).select_related('quay_face_axis'))
    selected = None
    if segments:
        def distance_to_segment(segment):
            chainage = project_rail(segment.quay_face_axis.points, coordinate)['chainage_m']
            low = min(segment.start_chainage_m, segment.end_chainage_m)
            high = max(segment.start_chainage_m, segment.end_chainage_m)
            return max(low - chainage, chainage - high, 0)
        selected = min(segments, key=distance_to_segment)
    water_side = selected.water_side if selected else (
        quay.metadata.get('editor_generator', {}).get('water_side', BerthSegment.WATER_LEFT)
    )
    face = selected.quay_face_axis if selected else (
        axis if axis.axis_kind == LinearAxis.QUAY_FACE else
        quay.linear_axes.filter(axis_kind=LinearAxis.QUAY_FACE, is_enabled=True).first()
    )
    face_points = face.points if face and len(face.points) >= 2 else points
    rail_heading = project_rail(points, coordinate)['heading_deg']
    face_heading = project_rail(face_points, coordinate)['heading_deg']
    same_direction = math.cos(math.radians(rail_heading - face_heading)) >= 0
    offset = -90.0 if water_side == BerthSegment.WATER_LEFT else 90.0
    return {'heading_offset_deg': offset if same_direction else -offset,
            'faces_water_side': True, 'water_side': water_side}


def che_rail_motion(che):
    mode = rail_mode(che.equipment_type.equipment_type)
    result = {'mode': mode, 'assigned_axis_id': None, 'rails': []}
    if mode == 'free' or not che.location_id:
        return result
    ancestors = []
    asset = che.current_asset
    while asset and asset.pk not in ancestors:
        ancestors.append(asset.pk)
        asset = asset.parent
    layout_id = che.current_asset.layout_version_id if che.current_asset else None
    scopes = list(CheOperatingScope.objects.filter(che_id=che.pk, is_enabled=True).select_related('axis', 'asset')) if che.pk else []
    if not scopes:
        scopes = list(CheOperatingScope.objects.filter(equipment_type=che.equipment_type, is_enabled=True).select_related('axis', 'asset'))
    kinds = rail_axis_kinds(che.equipment_type.equipment_type)
    candidates = []
    if scopes:
        for scope in scopes:
            axes = [scope.axis] if scope.axis_id else scope.asset.linear_axes.all()
            for axis in axes:
                if axis and axis.asset.layout_version.location_id == che.location_id and (layout_id is None or axis.asset.layout_version_id == layout_id):
                    if mode == 'repositionable' or axis.asset_id in ancestors or scope.asset_id in ancestors or not ancestors:
                        candidates.append((axis, scope))
    else:
        axes = LinearAxis.objects.filter(asset__layout_version__location_id=che.location_id, is_enabled=True).select_related('asset')
        axes = axes.filter(asset__layout_version_id=layout_id) if layout_id else axes.filter(asset__layout_version__is_current=True)
        if mode == 'fixed':
            axes = axes.filter(asset_id__in=ancestors)
        candidates = [(axis, None) for axis in axes]
    candidates.sort(key=lambda pair: (kinds.index(pair[0].axis_kind) if pair[0].axis_kind in kinds else 99, pair[0].pk))
    seen = set()
    for axis, scope in candidates:
        if axis.pk in seen or not axis.is_enabled or axis.axis_kind not in kinds:
            continue
        points = axis.points
        if len(points) < 2 or any(len(p) < 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p[:2]) for p in points):
            continue
        clipped = _clip_axis(points, scope.min_chainage_m if scope else None, scope.max_chainage_m if scope else None)
        if not clipped:
            continue
        seen.add(axis.pk)
        length = polyline_length_m(points)
        coordinate = [che.longitude, che.latitude] if che.longitude is not None and che.latitude is not None else clipped[0]
        facing = _sts_facing(axis, clipped, coordinate) if che.equipment_type.equipment_type in {'STS', 'QC'} else {
            'heading_offset_deg': 0.0, 'faces_water_side': False, 'water_side': None,
        }
        result['rails'].append({'axis_id': axis.pk, 'asset_id': axis.asset_id, 'axis_kind': axis.axis_kind, 'points': clipped,
                                'geometry': axis.asset.geometry,
                                'can_exit': mode == 'repositionable' and (scope.can_reposition if scope else True),
                                'entry_tolerance_m': max(0.5, che.equipment_type.width_m / 2),
                                'entry_at_start': not scope or scope.min_chainage_m is None or scope.min_chainage_m <= 0,
                                'entry_at_end': not scope or scope.max_chainage_m is None or scope.max_chainage_m >= length,
                                'speed_limit_kph': axis.speed_limit_kph, **facing})
    assigned = [rail for rail in result['rails'] if rail['asset_id'] in ancestors]
    if not assigned and mode == 'fixed' and scopes:
        assigned = result['rails']
    if assigned:
        priority = min(kinds.index(rail['axis_kind']) for rail in assigned)
        assigned = [rail for rail in assigned if kinds.index(rail['axis_kind']) == priority]
        coordinate = [che.longitude, che.latitude] if che.longitude is not None and che.latitude is not None else assigned[0]['points'][0]
        result['assigned_axis_id'] = min(assigned, key=lambda rail: project_rail(rail['points'], coordinate)['distance_m'])['axis_id']
    return result


def che_rail_assignment(che):
    """Describe the selected rail pose and gantry envelope in full-axis meters."""
    motion = che_rail_motion(che)
    axis_id = motion['assigned_axis_id']
    if not axis_id:
        return None
    axis = LinearAxis.objects.select_related('asset').filter(pk=axis_id).first()
    if not axis or len(axis.points or []) < 2:
        return None
    length = polyline_length_m(axis.points)
    scope = che.operating_scopes.filter(axis=axis, is_enabled=True).order_by('pk').first()
    coordinate = (
        [che.longitude, che.latitude]
        if che.longitude is not None and che.latitude is not None
        else axis.points[0]
    )
    position = project_rail(axis.points, coordinate)['chainage_m']
    return {
        'axis_id': axis.pk,
        'axis_code': axis.code,
        'axis_name': axis.name,
        'asset_id': axis.asset_id,
        'position_m': position,
        'min_position_m': scope.min_chainage_m if scope and scope.min_chainage_m is not None else 0.0,
        'max_position_m': scope.max_chainage_m if scope and scope.max_chainage_m is not None else length,
        'rail_length_m': length,
    }


def _asset_related(first, second):
    if not first or not second:
        return False
    cursor = first
    seen = set()
    while cursor and cursor.pk not in seen:
        if cursor.pk == second.pk:
            return True
        seen.add(cursor.pk)
        cursor = cursor.parent
    cursor = second
    seen.clear()
    while cursor and cursor.pk not in seen:
        if cursor.pk == first.pk:
            return True
        seen.add(cursor.pk)
        cursor = cursor.parent
    return False


def validate_che_rail_assignment(che, axis, position_m, min_position_m, max_position_m):
    errors = {}
    mode = rail_mode(che.equipment_type.equipment_type)
    if mode == 'free':
        errors['rail_axis'] = 'This equipment type is not constrained to a rail.'
    if not che.current_asset_id:
        errors['current_asset'] = 'Assign the equipment to a quay, yard block, or rail area first.'
    if not axis:
        errors['rail_axis'] = 'Select a rail axis.'
    elif not axis.is_enabled:
        errors['rail_axis'] = 'Select an enabled rail axis.'
    elif axis.axis_kind not in rail_axis_kinds(che.equipment_type.equipment_type):
        errors['rail_axis'] = 'The selected axis is not valid for this equipment type.'
    elif che.location_id and axis.asset.layout_version.location_id != che.location_id:
        errors['rail_axis'] = 'The selected rail belongs to another terminal.'
    elif che.current_asset_id and not _asset_related(axis.asset, che.current_asset):
        errors['rail_axis'] = 'The selected rail must belong to the assigned area or its hierarchy.'

    length = polyline_length_m(axis.points) if axis and len(axis.points or []) >= 2 else 0.0
    low = 0.0 if min_position_m is None else float(min_position_m)
    high = length if max_position_m is None else float(max_position_m)
    position = low if position_m is None else float(position_m)
    if low < 0 or low > length:
        errors['rail_min_position_m'] = f'Minimum gantry position must be between 0 and {length:.2f} m.'
    if high < 0 or high > length:
        errors['rail_max_position_m'] = f'Maximum gantry position must be between 0 and {length:.2f} m.'
    if low > high:
        errors['rail_max_position_m'] = 'Maximum gantry position must be at least the minimum.'
    if position < low or position > high:
        errors['rail_position_m'] = 'Rail position must be inside the gantry travel envelope.'
    if errors:
        raise ValidationError(errors)
    return position, low, high


def assign_che_to_rail(che, axis, position_m=None, min_position_m=None, max_position_m=None):
    """Persist one primary rail scope and project the CHE pose onto that axis."""
    position, low, high = validate_che_rail_assignment(
        che, axis, position_m, min_position_m, max_position_m,
    )
    scope = che.operating_scopes.filter(asset=che.current_asset).order_by('pk').first()
    if not scope:
        scope = CheOperatingScope(che=che, asset=che.current_asset)
    scope.axis = axis
    scope.min_chainage_m = low
    scope.max_chainage_m = high
    scope.is_enabled = True
    scope.full_clean()
    scope.save()
    che.operating_scopes.filter(asset=che.current_asset).exclude(pk=scope.pk).delete()

    point = point_along_axis(axis.points, position)
    che.longitude, che.latitude = point
    aligned = align_che_pose(che, force=True)
    if aligned:
        che.longitude = aligned['longitude']
        che.latitude = aligned['latitude']
        che.rotation = aligned['rotation']
    che.save(update_fields=['longitude', 'latitude', 'rotation'])
    return scope


def align_che_pose(che, force=False):
    motion = che_rail_motion(che)
    if che.longitude is None or che.latitude is None:
        return None
    rail = next((rail for rail in motion['rails'] if rail['axis_id'] == motion['assigned_axis_id']), None)
    if not rail:
        return None
    projection = project_rail(rail['points'], [che.longitude, che.latitude])
    if motion['mode'] == 'fixed' or force or projection['distance_m'] <= rail['entry_tolerance_m']:
        return {'longitude': projection['point'][0], 'latitude': projection['point'][1],
                'rotation': (projection['heading_deg'] + rail.get('heading_offset_deg', 0)) % 360}
    return None
