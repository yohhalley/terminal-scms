"""Layout 90° rotation bug (S2 stretch, reported by Yael 31 Oct).

The terminal editor (``terminal_editor_map_tools.js``) draws a yard block's
grid and a quay's water side and lanes from the asset polygon's first vertex
(the anchor) along the asset heading:

* yard block: ``yardFootprint`` is anchor -> +bay axis -> +row axis, where the
  bay axis is the heading (``bay_use_heading_axis``) or the right of it;
* quay: ``orientedFootprint`` is anchor -> +heading (quay length) -> inland,
  with the water just outside the anchor edge.

The import_v1 seed must follow the same convention, otherwise the generated
piles, water and lanes come out 90° from the drawn polygons.
"""

import math

from django.test import TestCase

from minitos_core.models import BerthSegment, LayoutAsset, Location, Pile, StorageGrid
from minitos_core.scenarios import import_v1, reset_scenario
from minitos_core.services.coordinates import geographic_to_local


def _vectors(heading_deg):
    heading = math.radians(heading_deg)
    return (math.sin(heading), math.cos(heading)), (math.cos(heading), -math.sin(heading))


def _bearing(start, end):
    return math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) % 360


def _angle_between(a, b):
    return abs((a - b + 180) % 360 - 180)


class ImportV1LayoutOrientationTests(TestCase):
    def setUp(self):
        reset_scenario('import_v1')
        self.location = Location.objects.get(name=import_v1.LOCATION_NAME)

    def local(self, longitude, latitude):
        return geographic_to_local(self.location, latitude, longitude)

    def ring(self, asset):
        return [self.local(*point) for point in asset.geometry['coordinates'][0][:-1]]

    def test_block01_piles_lie_inside_the_block_polygon(self):
        grid = StorageGrid.objects.get(layout_asset__code=import_v1.EXCEPTION_BLOCK_NAME)
        ring = self.ring(grid.layout_asset)
        xs, ys = [x for x, _ in ring], [y for _, y in ring]
        for pile in Pile.objects.filter(storage_grid=grid):
            x, y = self.local(pile.longitude, pile.latitude)
            with self.subTest(pile=pile.get_pile_name()):
                self.assertTrue(min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys),
                                f'pile at ({x:.1f}, {y:.1f}) is outside the polygon '
                                f'x {min(xs):.0f}..{max(xs):.0f}, y {min(ys):.0f}..{max(ys):.0f}')

    def test_block01_bays_run_along_the_polygon_long_side_as_the_editor_draws_it(self):
        grid = StorageGrid.objects.get(layout_asset__code=import_v1.EXCEPTION_BLOCK_NAME)
        asset = grid.layout_asset
        self.assertEqual(asset.heading_deg, grid.heading_deg)
        forward, right = _vectors(grid.heading_deg)
        bay_axis = forward if grid.bay_use_heading_axis else right
        ring = self.ring(asset)
        # yardFootprint: the first polygon edge runs along the bay axis.
        self.assertLess(_angle_between(_bearing(ring[0], ring[1]), _bearing((0, 0), bay_axis)), 0.5)
        first = Pile.objects.get(storage_grid=grid, bay=1, row=1)
        next_bay = Pile.objects.get(storage_grid=grid, bay=2, row=1)
        self.assertLess(_angle_between(
            _bearing(self.local(first.longitude, first.latitude), self.local(next_bay.longitude, next_bay.latitude)),
            _bearing(ring[0], ring[1]),
        ), 0.5)

    def test_quay01_heading_follows_the_quay_face_and_the_polygon_starts_on_it(self):
        quay = LayoutAsset.objects.get(code='QUAY-01')
        berth = BerthSegment.objects.get(quay_asset=quay)
        face = [self.local(*point) for point in berth.quay_face_axis.points]
        ring = self.ring(quay)
        # orientedFootprint: anchor (ring[0]) at the face start, first edge along the heading.
        self.assertLess(_angle_between(quay.heading_deg, _bearing(face[0], face[-1])), 0.5)
        self.assertLess(math.dist(ring[0], face[0]), 0.01)
        self.assertLess(math.dist(ring[1], face[-1]), 0.01)
        # Water on the left of the heading, so the polygon (land) lies to the right.
        self.assertEqual(berth.water_side, BerthSegment.WATER_LEFT)
        _, right = _vectors(quay.heading_deg)
        inland = (ring[2][0] - ring[1][0], ring[2][1] - ring[1][1])
        self.assertGreater(inland[0] * right[0] + inland[1] * right[1], 0)
