"""Render a read-only browser fixture using the real editor template and scripts."""

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings_test')

import django
django.setup()
from django.template.loader import render_to_string


def point(east, north):
    return [-102.15 + east / 105900, 17.95 + north / 110574]


def rectangle(east, north, width, length):
    ring = [point(east, north), point(east, north + length),
            point(east + width, north + length), point(east + width, north)]
    return {'type': 'Polygon', 'coordinates': [ring + [ring[0]]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--base-url', default='http://localhost:8000/')
    args = parser.parse_args()
    location = {'id': 1, 'name': 'Layout visibility test fixture',
                'map_origin_latitude': 17.951, 'map_origin_longitude': -102.148,
                'default_zoom': 18, 'map_width_m': 500, 'map_height_m': 300,
                'map_units_per_meter': 1, 'angle': 0, 'timezone': 'UTC'}
    kinds = [('terminal', 0, 0, 500, 300), ('quay', 0, 0, 25, 280),
             ('yard_block', 90, 50, 23.5, 139), ('gate_area', 330, 210, 25, 60),
             ('rail_area', 400, 20, 20, 180), ('building', 240, 60, 35, 30),
             ('parking', 220, 130, 100, 50)]
    assets = [{'id': index + 1, 'code': kind.upper(), 'name': kind.replace('_', ' ').title(),
               'asset_kind': kind, 'geometry': rectangle(east, north, width, length),
               'heading_deg': 0, 'metadata': {}, 'is_visible': True, 'is_active': True}
              for index, (kind, east, north, width, length) in enumerate(kinds)]
    anchor = point(90, 50)
    assets[2]['storage_grid'] = {'id': 1, 'longitude': anchor[0], 'latitude': anchor[1],
        'heading_deg': 0, 'max_bays': 20, 'max_rows': 8, 'max_tier': 4,
        'pile_length_m': 6, 'pile_width_m': 2.5, 'bay_spacing_m': 1, 'row_spacing_m': 0.5,
        'bay_use_heading_axis': True, 'initial_bay': '1', 'initial_row': 'A',
        'bay_naming': '1,3,5...', 'row_naming': 'A,B,C...', 'bay_order': 'forward', 'row_order': 'forward'}
    interfaces = []
    for identifier, asset_id, east, north, length, lanes, count in [(1, 3, 88, 50, 139, 4, 8), (2, 2, 27, 0, 280, 2, 10), (3, 5, 398, 20, 180, 2, 3)]:
        side = 'right' if asset_id == 2 else 'left'
        sign = 1 if side == 'right' else -1
        interface = {'id': identifier, 'code': f'TP{identifier}', 'name': f'{assets[asset_id - 1]["name"]} transfer', 'asset': asset_id,
            'side': side, 'lane_count': lanes, 'lane_spacing_m': 3.5, 'capacity': lanes * count,
            'interface_kind': 'ground_buffer' if asset_id == 3 else 'vehicle_lane',
            'geometry': {'type': 'LineString', 'coordinates': [point(east, north), point(east, north + length)]},
            'positions': [{'id': identifier * 100 + lane * count + i, 'code': f'TP{identifier}.L{lane + 1}.{i + 1}',
                           'lane_index': lane + 1, 'position_index': i + 1,
                           'longitude': point(east + sign * lane * 3.5, north + (i + .5) * length / count)[0],
                           'latitude': point(east + sign * lane * 3.5, north + (i + .5) * length / count)[1]} for lane in range(lanes) for i in range(count)]}
        interfaces.append(interface)
        assets[asset_id - 1]['transfer_interfaces'] = [interface]
        assets[asset_id - 1]['metadata']['editor_transfers'] = {'sides': {side: {'enabled': True,
            'interface_kind': interface['interface_kind'], 'offset_m': 2, 'lane_count': lanes,
            'lane_spacing_m': 3.5, 'position_mode': 'custom', 'position_count': count}}}
    assets[1]['metadata']['editor_generator'] = {'kind': 'quay', 'bollard_count': 15, 'default_bollard_spacing_m': 20, 'width_m': 25, 'water_side': 'left'}
    assets[4]['metadata']['editor_generator'] = {'kind': 'rail_area', 'track_count': 2, 'track_length_m': 180, 'track_spacing_m': 5}
    axes = [{'id': 1, 'asset': 2, 'axis_kind': 'quay_face', 'code': 'QUAY.FACE', 'name': 'Quay face',
        'points': [point(0, 10), point(0, 270)], 'metadata': {}, 'markers': [
            {'id': i + 1, 'marker_kind': 'bollard', 'name': str(i + 1), 'longitude': point(0, 10 + i * 20)[0],
             'latitude': point(0, 10 + i * 20)[1]} for i in range(14)]}]
    axes.extend([{'id': 2, 'asset': 3, 'axis_kind': 'crane_rail', 'code': 'BLOCK.RAIL', 'name': 'Block rail',
                  'points': [point(101.75, 50), point(101.75, 189)], 'metadata': {}, 'markers': []},
                 {'id': 3, 'asset': 2, 'axis_kind': 'crane_rail', 'code': 'QUAY.RAIL', 'name': 'STS rail',
                  'points': [point(12, 10), point(12, 270)], 'metadata': {}, 'markers': []}])
    data = {'/minitos_core/api/location/1/': location}
    equipment_types = [{'id': index + 1, 'name': kind, 'equipment_type': kind,
                        'length_m': length, 'width_m': width}
                       for index, (kind, length, width) in enumerate([('TT', 8, 3), ('SC', 10, 5), ('RTG', 12, 23), ('QC', 27, 18)])]
    ches = []
    for index, (east, north, rotation, asset_id) in enumerate([(250, 145, 90, 7), (280, 160, 0, 7), (102, 105, 180, 3), (12, 170, 0, 2)]):
        coordinate = point(east, north)
        che_type = equipment_types[index]
        ches.append({'pk': index + 1, 'name': f'{che_type["name"]}01', 'equipment_type': che_type['id'],
                     'equipment_type_name': che_type['name'], 'equipment_type_details': che_type,
                     'current_asset': asset_id, 'longitude': coordinate[0], 'latitude': coordinate[1],
                     'rotation': rotation, 'powered_on': index != 2})
    data['/minitos_core/equipment-types/'] = equipment_types
    for endpoint, value in {'layout-versions': [{'id': 1, 'is_current': True, 'status': 'draft'}],
                           'layout-assets': assets, 'linear-axes': axes, 'transfer-interfaces': interfaces,
                           'ches': ches}.items():
        data[f'/minitos_core/locations/1/{endpoint}/'] = value
    html = render_to_string('terminal_editor.html', {'location': SimpleNamespace(**location),
        'location_id': 1, 'layout': SimpleNamespace(id=1, version=1, status='draft'), 'can_edit_terminal': True})
    bootstrap = f'''<base href="{args.base_url}"><script>
const fixtureData = {json.dumps(data)};
const originalFetch = window.fetch.bind(window);
window.fetch = (url, options = {{}}) => {{
    if (!String(url).startsWith('/minitos_core/')) return originalFetch(url, options);
    if (options.method && options.method !== 'GET') return Promise.resolve(new Response('{{"detail":"Read-only test fixture"}}', {{status:403}}));
    return Promise.resolve(new Response(JSON.stringify(fixtureData[url] || []), {{status:200, headers:{{'Content-Type':'application/json'}}}}));
}};
</script>'''
    args.output.write_text(html.replace('<head>', '<head>' + bootstrap), encoding='utf-8')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
