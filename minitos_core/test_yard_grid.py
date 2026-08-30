from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from minitos_core.models import Area, Block, Che, ContainerVisit, EquipmentType, Location, ParkingArea, Pile, Position, Road, TerminalConnection
from minitos_core.services import geographic_to_local, local_to_geographic, offset_geographic, sync_yard_grid


class YardGridServiceTests(APITestCase):
    def setUp(self):
        self.location = Location.objects.create(
            name='Grid Terminal', location_type='TL',
            latitude=40, longitude=-3, map_origin_latitude=40, map_origin_longitude=-3,
        )
        self.block = Block.objects.create(
            location=self.location,
            name='A01',
            max_bays=2,
            max_rows=2,
            max_tier=2,
            latitude=40,
            longitude=-3,
            bay_use_heading_axis=True,
            bay_naming=Block.LETTERS,
            initial_bay='C',
        )

    def test_sync_creates_named_grid_and_normal_save_preserves_it(self):
        sync_yard_grid(self.block)
        pile = Pile.objects.get(block=self.block, bay=1, row=1)
        position = Position.objects.get(pile=pile, tier=1)

        self.assertEqual(self.block.piles.count(), 4)
        self.assertEqual(Position.objects.filter(pile__block=self.block).count(), 8)
        self.assertEqual(pile.get_bay_name(), 'C')
        expected_lat, expected_lon = offset_geographic(
            self.block.latitude, self.block.longitude,
            self.block.pile_width_m / 2, self.block.pile_length_m / 2,
        )
        self.assertAlmostEqual(pile.latitude, expected_lat)
        self.assertAlmostEqual(pile.longitude, expected_lon)

        self.block.heading_deg = 15
        self.block.save(update_fields=['heading_deg'])
        self.assertTrue(Position.objects.filter(pk=position.pk).exists())

    def test_sync_rejects_removing_reserved_positions(self):
        sync_yard_grid(self.block)
        protected = Position.objects.get(pile__block=self.block, pile__bay=2, pile__row=2, tier=2)
        protected.is_reserved = True
        protected.save(update_fields=['is_reserved'])
        self.block.max_bays = 1

        with self.assertRaises(ValidationError):
            sync_yard_grid(self.block)

        self.assertTrue(Position.objects.filter(pk=protected.pk).exists())


class YardBlockAreaApiTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username='yard-editor', password='test')
        self.client.force_authenticate(user=user)
        self.location = Location.objects.create(
            name='Editor Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3,
        )

    def test_create_yard_area_builds_block_piles_and_positions(self):
        response = self.client.post(
            reverse('minitos_core:area-list', args=[self.location.pk]),
            {
                'location': self.location.pk,
                'name': 'North Block',
                'area_type': Area.YARD_BLOCK,
                'area': 1200,
                'coordinates': [[-3, 40], [-2.9998, 40], [-2.9998, 40.0005], [-3, 40.0005]],
                'is_visible': True,
                'yard_grid': {
                    'max_bays': 3,
                    'max_rows': 2,
                    'max_tier': 4,
                    'latitude': 40,
                    'longitude': -3,
                    'heading_deg': 0,
                    'slot_heading_deg': 10,
                    'pile_length_m': 6.058,
                    'pile_width_m': 2.438,
                    'bay_spacing_m': 0.3,
                    'row_spacing_m': 0.3,
                    'initial_bay': '1',
                    'initial_row': 'A',
                    'bay_naming': Block.ODD_NUMBERS,
                    'row_naming': Block.LETTERS,
                    'bay_order': Block.FORWARD,
                    'row_order': Block.FORWARD,
                    'bay_use_heading_axis': True,
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        area = Area.objects.get(pk=response.data['id'])
        self.assertIsNotNone(area.block_id)
        self.assertEqual(area.block.piles.count(), 6)
        self.assertEqual(Position.objects.filter(pile__block=area.block).count(), 24)
        self.assertEqual(response.data['block_config']['slot_heading_deg'], 10)

    def test_update_yard_area_moves_rotates_and_resizes_grid(self):
        create_response = self.client.post(
            reverse('minitos_core:area-list', args=[self.location.pk]),
            {
                'location': self.location.pk,
                'name': 'Mutable Block',
                'area_type': Area.YARD_BLOCK,
                'area': 300,
                'coordinates': [[-3, 40], [-2.9998, 40], [-2.9998, 40.0002], [-3, 40.0002]],
                'is_visible': True,
                'yard_grid': {
                    'max_bays': 2, 'max_rows': 2, 'max_tier': 2,
                    'latitude': 40, 'longitude': -3, 'heading_deg': 0, 'slot_heading_deg': 0,
                    'pile_length_m': 6, 'pile_width_m': 2.5,
                    'bay_spacing_m': 1, 'row_spacing_m': 0.5,
                    'initial_bay': '1', 'initial_row': 'A',
                    'bay_naming': Block.NUMBERS, 'row_naming': Block.LETTERS,
                    'bay_order': Block.FORWARD, 'row_order': Block.FORWARD,
                    'bay_use_heading_axis': True,
                },
            },
            format='json',
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        area = Area.objects.get(pk=create_response.data['id'])
        block = area.block
        preserved_position_ids = {
            (position.pile.bay, position.pile.row, position.tier): position.pk
            for position in Position.objects.filter(pile__block=block).select_related('pile')
        }
        original_centers = {
            (pile.bay, pile.row): (pile.latitude, pile.longitude)
            for pile in block.piles.all()
        }

        moved_latitude = 40.0004
        moved_longitude = -2.9996
        update_response = self.client.put(
            reverse('minitos_core:area-detail', args=[self.location.pk, area.pk]),
            {
                'location': self.location.pk,
                'name': area.name,
                'area_type': Area.YARD_BLOCK,
                'area': 900,
                'coordinates': [[-2.9996, 40.0004], [-2.9992, 40.0004], [-2.9992, 40.0008], [-2.9996, 40.0008]],
                'is_visible': True,
                'yard_grid': {
                    'max_bays': 4, 'max_rows': 3, 'max_tier': 3,
                    'latitude': moved_latitude, 'longitude': moved_longitude,
                    'heading_deg': 90, 'slot_heading_deg': 15,
                    'pile_length_m': 6, 'pile_width_m': 2.5,
                    'bay_spacing_m': 1.5, 'row_spacing_m': 0.75,
                    'initial_bay': '3', 'initial_row': 'C',
                    'bay_naming': Block.ODD_NUMBERS, 'row_naming': Block.LETTERS,
                    'bay_order': Block.REVERSE, 'row_order': Block.FORWARD,
                    'bay_use_heading_axis': True,
                    'padding_bottom_m': 2, 'padding_left_m': 1,
                },
            },
            format='json',
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK, update_response.data)
        block.refresh_from_db()
        self.assertEqual(block.piles.count(), 12)
        self.assertEqual(Position.objects.filter(pile__block=block).count(), 36)
        self.assertEqual(block.heading_deg, 90)
        self.assertEqual(block.slot_heading_deg, 15)
        for key, position_id in preserved_position_ids.items():
            self.assertEqual(
                Position.objects.get(pile__block=block, pile__bay=key[0], pile__row=key[1], tier=key[2]).pk,
                position_id,
            )
        first_pile = block.piles.get(bay=1, row=1)
        expected_latitude, expected_longitude = offset_geographic(
            moved_latitude,
            moved_longitude,
            2 + block.pile_length_m / 2,
            -(1 + block.pile_width_m / 2),
        )
        self.assertAlmostEqual(first_pile.latitude, expected_latitude)
        self.assertAlmostEqual(first_pile.longitude, expected_longitude)
        self.assertNotEqual((first_pile.latitude, first_pile.longitude), original_centers[(1, 1)])
        self.assertEqual(first_pile.get_bay_name(), '9')
        self.assertEqual(first_pile.get_row_name(), 'C')

        shrink_payload = update_response.data.copy()
        shrink_payload['yard_grid'] = dict(update_response.data['block_config'])
        shrink_payload['yard_grid'].update({'max_bays': 2, 'max_rows': 1, 'max_tier': 1})
        shrink_payload['yard_grid'].pop('piles', None)
        shrink_response = self.client.put(
            reverse('minitos_core:area-detail', args=[self.location.pk, area.pk]),
            shrink_payload,
            format='json',
        )
        self.assertEqual(shrink_response.status_code, status.HTTP_200_OK, shrink_response.data)
        block.refresh_from_db()
        self.assertEqual(block.piles.count(), 2)
        self.assertEqual(Position.objects.filter(pile__block=block).count(), 2)


class TerminalMapApiTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username='map-editor', password='test')
        self.client.force_authenticate(user=user)
        self.location = Location.objects.create(
            name='Graph Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3,
        )
        self.che_type = EquipmentType.objects.create(name='Terminal Tractor', equipment_type='TT')

    def test_road_create_and_update_syncs_endpoint_nodes(self):
        url = reverse('minitos_core:road-list', args=[self.location.pk])
        response = self.client.post(url, {
            'location': self.location.pk, 'name': 'Main Spine',
            'points': [[-3, 40], [-2.9999, 40.0001], [-2.9998, 40.0001]],
            'lane_count': 2, 'capacity': 8, 'direction': Road.FORWARD,
            'allowed_che_types': [self.che_type.pk],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        road = Road.objects.get(pk=response.data['id'])
        self.assertEqual((road.start_node.longitude, road.start_node.latitude), (-3, 40))
        self.assertEqual((road.end_node.longitude, road.end_node.latitude), (-2.9998, 40.0001))

        response = self.client.patch(
            reverse('minitos_core:road-detail', args=[self.location.pk, road.pk]),
            {'points': [[-3.0001, 39.9999], [-2.9998, 40], [-2.9995, 40.0002]]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        road.refresh_from_db()
        road.start_node.refresh_from_db()
        road.end_node.refresh_from_db()
        self.assertEqual((road.start_node.longitude, road.start_node.latitude), (-3.0001, 39.9999))
        self.assertEqual((road.end_node.longitude, road.end_node.latitude), (-2.9995, 40.0002))

    def test_parking_area_and_connection(self):
        response = self.client.post(
            reverse('minitos_core:area-list', args=[self.location.pk]),
            {
                'location': self.location.pk, 'name': 'Tractor Parking', 'area_type': Area.PARKING, 'area': 100,
                'coordinates': [[-3, 40], [-2.9999, 40], [-2.9999, 40.0001], [-3, 40.0001]],
                'parking_settings': {'capacity': 5, 'allowed_che_types': [self.che_type.pk]},
            }, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        parking = ParkingArea.objects.get(area_id=response.data['id'])
        self.assertEqual(parking.capacity, 5)
        self.assertEqual(list(parking.allowed_che_types.values_list('pk', flat=True)), [self.che_type.pk])

        road_response = self.client.post(
            reverse('minitos_core:road-list', args=[self.location.pk]),
            {'location': self.location.pk, 'name': 'Access', 'points': [[-3, 40], [-2.9998, 40.0002]], 'lane_count': 1, 'capacity': 2, 'direction': Road.BOTH},
            format='json'
        )
        node_id = road_response.data['start_node']
        connection = self.client.post(
            reverse('minitos_core:terminal-connection-list'),
            {'node': node_id, 'parking_area': parking.pk, 'is_bidirectional': True, 'cost': 1},
            format='json'
        )
        self.assertEqual(connection.status_code, status.HTTP_201_CREATED, connection.data)
        self.assertTrue(TerminalConnection.objects.filter(pk=connection.data['id']).exists())


class CoordinateProjectionTests(APITestCase):
    def test_location_rotation_round_trip(self):
        location = Location.objects.create(
            name='Rotated Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3, angle=32,
        )
        latitude, longitude = local_to_geographic(location, 250, 480)
        x_m, y_m = geographic_to_local(location, latitude, longitude)
        self.assertAlmostEqual(x_m, 250, places=5)
        self.assertAlmostEqual(y_m, 480, places=5)

    def test_movable_object_apis_accept_geographic_positions(self):
        user = User.objects.create_user(username='simulator', password='test')
        self.client.force_authenticate(user=user)
        location = Location.objects.create(
            name='Simulator Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3,
        )
        equipment_type = EquipmentType.objects.create(name='Test Tractor', equipment_type='TT')
        che = Che.objects.create(name='TT-GPS', equipment_type=equipment_type, location=location)
        visit = ContainerVisit.objects.create(location=location)

        che_response = self.client.patch(
            reverse('minitos_core:che-detail', args=[location.pk, che.pk]),
            {'latitude': 40.0002, 'longitude': -2.9998}, format='json',
        )
        visit_response = self.client.patch(
            reverse('minitos_core:container_visit-detail', args=[location.pk, visit.pk]),
            {'latitude': 40.0003, 'longitude': -2.9997}, format='json',
        )

        self.assertEqual(che_response.status_code, status.HTTP_200_OK, che_response.data)
        self.assertEqual(visit_response.status_code, status.HTTP_200_OK, visit_response.data)
        self.assertNotIn('x_position', che_response.data)
        self.assertNotIn('y_position', visit_response.data)
