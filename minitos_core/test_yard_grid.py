from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from minitos_core.models import Area, Block, EquipmentType, Location, ParkingArea, Pile, Position, Road, TerminalConnection
from minitos_core.services import sync_yard_grid


class YardGridServiceTests(APITestCase):
    def setUp(self):
        self.location = Location.objects.create(name='Grid Terminal', location_type='TL')
        self.block = Block.objects.create(
            location=self.location,
            name='A01',
            max_bays=2,
            max_rows=2,
            max_tier=2,
            x_position=100,
            y_position=200,
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
        self.assertAlmostEqual(pile.x_position, 100 + self.block.pile_width_m / 2)
        self.assertAlmostEqual(pile.y_position, 200 + self.block.pile_length_m / 2)

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
            name='Editor Terminal', location_type='TL', latitude=40, longitude=-3
        )

    def test_create_yard_area_builds_block_piles_and_positions(self):
        response = self.client.post(
            reverse('minitos_core:area-list', args=[self.location.pk]),
            {
                'location': self.location.pk,
                'name': 'North Block',
                'area_type': Area.YARD_BLOCK,
                'area': 1200,
                'coordinates': [[10, 20], [30, 20], [30, 80], [10, 80]],
                'is_visible': True,
                'yard_grid': {
                    'max_bays': 3,
                    'max_rows': 2,
                    'max_tier': 4,
                    'x_position': 10,
                    'y_position': 20,
                    'anchor_lat': 40,
                    'anchor_lon': -3,
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


class TerminalMapApiTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username='map-editor', password='test')
        self.client.force_authenticate(user=user)
        self.location = Location.objects.create(name='Graph Terminal', location_type='TL')
        self.che_type = EquipmentType.objects.create(name='Terminal Tractor', equipment_type='TT')

    def test_road_create_and_update_syncs_endpoint_nodes(self):
        url = reverse('minitos_core:road-list', args=[self.location.pk])
        response = self.client.post(url, {
            'location': self.location.pk, 'name': 'Main Spine', 'points': [[0, 0], [10, 5], [20, 5]],
            'lane_count': 2, 'capacity': 8, 'direction': Road.FORWARD,
            'allowed_che_types': [self.che_type.pk],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        road = Road.objects.get(pk=response.data['id'])
        self.assertEqual((road.start_node.x_position, road.start_node.y_position), (0, 0))
        self.assertEqual((road.end_node.x_position, road.end_node.y_position), (20, 5))

        response = self.client.patch(
            reverse('minitos_core:road-detail', args=[self.location.pk, road.pk]),
            {'points': [[2, 3], [12, 8], [30, 9]]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        road.refresh_from_db()
        road.start_node.refresh_from_db()
        road.end_node.refresh_from_db()
        self.assertEqual((road.start_node.x_position, road.start_node.y_position), (2, 3))
        self.assertEqual((road.end_node.x_position, road.end_node.y_position), (30, 9))

    def test_parking_area_and_connection(self):
        response = self.client.post(
            reverse('minitos_core:area-list', args=[self.location.pk]),
            {
                'location': self.location.pk, 'name': 'Tractor Parking', 'area_type': Area.PARKING, 'area': 100,
                'coordinates': [[0, 0], [10, 0], [10, 10], [0, 10]],
                'parking_settings': {'capacity': 5, 'allowed_che_types': [self.che_type.pk]},
            }, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        parking = ParkingArea.objects.get(area_id=response.data['id'])
        self.assertEqual(parking.capacity, 5)
        self.assertEqual(list(parking.allowed_che_types.values_list('pk', flat=True)), [self.che_type.pk])

        road_response = self.client.post(
            reverse('minitos_core:road-list', args=[self.location.pk]),
            {'location': self.location.pk, 'name': 'Access', 'points': [[10, 10], [20, 20]], 'lane_count': 1, 'capacity': 2, 'direction': Road.BOTH},
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
