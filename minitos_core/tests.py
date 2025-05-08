from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.core.exceptions import ValidationError
from minitos_core.models import (
    ContainerUnit, ISOType, ContainerOwner, Location, Block, Pile, 
    Position, Che, WorkQueue, Order, OrderLeg, ContainerVisit, EquipmentType
)
import json

class BaseTestCase(APITestCase):
    def setUp(self):
        # Create a test user
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # Create test location
        self.location = Location.objects.create(
            name='Test Terminal',
            location_type='TL'
        )

class ContainerTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.iso_type = ISOType.objects.create(
            iso_type='22R1',
            length='20',
            height='HC'
        )
        self.owner = ContainerOwner.objects.create(
            name='Test Shipping Line',
            code='TEST',
            is_shippingline=True
        )
        self.container = ContainerUnit.objects.create(
            iso_type=self.iso_type,
            owner=self.owner,
            container_number='TEST0000001',
            is_active=True,
            is_in_terminal=True
        )

    def test_container_list(self):
        url = reverse('minitos_core:containerunit-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Parse the response data if it's a string
        if isinstance(response.data, str):
            data = json.loads(response.data)
        else:
            data = response.data
            
        # Debug: Print the response data
        #print("\nResponse data:", data)
        #print("Owner ID:", self.owner.id)
        
        # Get the results array from the paginated response
        results = data.get('results', [])
        
        # Filter containers by owner to match our test data
        containers = [c for c in results if isinstance(c, dict) and c.get('owner') == self.owner.id]
        
        # Debug: Print filtered containers
        #print("Filtered containers:", containers)
        
        # Verify we have exactly one container
        self.assertEqual(len(containers), 1, f"Expected 1 container for owner {self.owner.id}, found {len(containers)}")
        
        # Verify the container details
        container = containers[0]
        self.assertEqual(container['container_number'], self.container.container_number)
        self.assertEqual(container['owner'], self.owner.id)

    def test_container_detail(self):
        url = reverse('minitos_core:containerunit-detail', args=[self.container.container_number])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['container_number'], self.container.container_number)

    def test_container_create(self):
        url = reverse('minitos_core:containerunit-list')
        data = {
            'iso_type': self.iso_type.id,
            'owner': self.owner.id,
            'container_number': 'TEST0000002',
            'is_active': True,
            'is_in_terminal': True
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ContainerUnit.objects.count(), 2)

class LocationTests(BaseTestCase):
    def test_location_list(self):
        url = reverse('minitos_core:location-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_location_detail(self):
        url = reverse('minitos_core:location-detail', args=[self.location.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Test Terminal')

class BlockTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        # Delete any existing blocks to ensure clean state
        Block.objects.all().delete()
        self.block = Block.objects.create(
            location=self.location,
            name='Block A',
            block_type='AB',
            max_bays=10,
            max_rows=10,
            max_tier=5
        )

    def test_block_list(self):
        url = reverse('minitos_core:block-list', args=[self.location.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Parse the response data if it's a string
        if isinstance(response.data, str):
            data = json.loads(response.data)
        else:
            data = response.data
            
        # Get the results array from the paginated response
        results = data.get('results', [])
        
        # Find our block in the response
        our_block = next((block for block in results if block['pk'] == self.block.id), None)
        self.assertIsNotNone(our_block, "Our test block was not found in the response")
        self.assertEqual(our_block['name'], 'Block A')
        self.assertEqual(our_block['block_type'], 'AB')

    def test_block_detail(self):
        url = reverse('minitos_core:block-detail', args=[self.location.id, self.block.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Block A')

class PileTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.block = Block.objects.create(
            location=self.location,
            name='Block A',
            block_type='AB',
            max_bays=10,
            max_rows=10,
            max_tier=5
        )
        # Create owner for container tests
        self.owner = ContainerOwner.objects.create(
            name='Test Shipping Line',
            code='TEST',
            is_shippingline=True
        )
        # The block's save method will have created piles automatically
        # Let's get the first pile for testing
        self.pile = Pile.objects.filter(block=self.block).first()

    def test_pile_list(self):
        url = reverse('minitos_core:pile-list', args=[self.location.id, self.block.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should have piles for all bays and rows
        expected_piles = self.block.max_bays * self.block.max_rows
        self.assertEqual(len(response.data), expected_piles)

    def test_pile_detail(self):
        url = reverse('minitos_core:pile-detail', args=[self.location.id, self.block.id, self.pile.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['bay'], self.pile.bay)
        self.assertEqual(response.data['row'], self.pile.row)

    def test_pile_recreation(self):
        # Delete a pile
        pile_to_delete = Pile.objects.filter(block=self.block, bay=1, row=1).first()
        pile_id = pile_to_delete.id
        pile_to_delete.delete()
        
        # Verify the pile is gone
        self.assertFalse(Pile.objects.filter(id=pile_id).exists())
        
        # Create a new pile in the same position
        new_pile = Pile.objects.create(
            block=self.block,
            bay=1,
            row=1,
            tiers=5,
            allows_in=True,
            allows_out=True,
            is_restricted=False
        )
        
        # Verify the new pile exists
        self.assertTrue(Pile.objects.filter(id=new_pile.id).exists())
        self.assertEqual(new_pile.bay, 1)
        self.assertEqual(new_pile.row, 1)

    def test_pile_container_size_restrictions(self):
        # Create a 20' container
        iso_type_20 = ISOType.objects.create(
            iso_type='22R1',
            length='20',
            height='HC'
        )
        container_20 = ContainerUnit.objects.create(
            iso_type=iso_type_20,
            owner=self.owner,
            container_number='TEST0000002',
            is_active=True,
            is_in_terminal=True
        )
        container_visit_20 = ContainerVisit.objects.create(
            category='S',
            container_number=container_20,
            location=self.location
        )

        # Create a 40' container
        iso_type_40 = ISOType.objects.create(
            iso_type='45R1',
            length='40',
            height='HC'
        )
        container_40 = ContainerUnit.objects.create(
            iso_type=iso_type_40,
            owner=self.owner,
            container_number='TEST0000003',
            is_active=True,
            is_in_terminal=True
        )
        container_visit_40 = ContainerVisit.objects.create(
            category='S',
            container_number=container_40,
            location=self.location
        )

        # Get positions in odd and even bays
        odd_bay_position = Position.objects.filter(pile__bay=1).first()
        even_bay_position = Position.objects.filter(pile__bay=2).first()

        # Clear any existing containers
        odd_bay_position.container_visit = None
        odd_bay_position.save()
        even_bay_position.container_visit = None
        even_bay_position.save()

        # Test 20' container in odd bay (should work)
        odd_bay_position.container_visit = container_visit_20
        odd_bay_position.save()
        odd_bay_position.refresh_from_db()
        self.assertEqual(odd_bay_position.container_visit, container_visit_20)

        # Clear the position for the next test
        odd_bay_position.container_visit = None
        odd_bay_position.save()

        # Test 20' container in even bay (should fail)
        even_bay_position.container_visit = container_visit_20
        with self.assertRaises(ValidationError) as context:
            even_bay_position.save()
        self.assertIn("20' containers are not allowed", str(context.exception))

        # Test 40' container in odd bay (should fail)
        odd_bay_position.container_visit = container_visit_40
        with self.assertRaises(ValidationError) as context:
            odd_bay_position.save()
        self.assertIn("40' containers are not allowed", str(context.exception))

        # Test 40' container in even bay (should work)
        even_bay_position.container_visit = container_visit_40
        even_bay_position.save()
        even_bay_position.refresh_from_db()
        self.assertEqual(even_bay_position.container_visit, container_visit_40)

    def test_pile_unique_constraint_across_locations(self):
        # Create a second location
        location2 = Location.objects.create(
            name='Test Terminal 2',
            location_type='TL'
        )

        # Create blocks with the same name in different locations
        block1 = Block.objects.create(
            location=self.location,
            name='Block B',  # Different name from setUp's Block A
            block_type='AB',
            max_bays=10,
            max_rows=10,
            max_tier=5
        )
        block2 = Block.objects.create(
            location=location2,
            name='Block B',  # Same name as block1 but in different location
            block_type='AB',
            max_bays=10,
            max_rows=10,
            max_tier=5
        )

        # Verify that both blocks have piles with the same bay/row combinations
        pile1 = Pile.objects.filter(block=block1, bay=1, row=1).first()
        pile2 = Pile.objects.filter(block=block2, bay=1, row=1).first()

        self.assertIsNotNone(pile1)
        self.assertIsNotNone(pile2)
        self.assertEqual(pile1.bay, pile2.bay)
        self.assertEqual(pile1.row, pile2.row)
        self.assertNotEqual(pile1.block.location, pile2.block.location)

        # Verify that we can't create a block with the same name in the same location
        with self.assertRaises(Exception):
            Block.objects.create(
                location=self.location,
                name='Block B',  # Same name and location as block1
                block_type='AB',
                max_bays=10,
                max_rows=10,
                max_tier=5
            )

class PositionTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        # Create a block with more bays than we need for testing
        self.block = Block.objects.create(
            location=self.location,
            name='Block B',
            block_type='AB',
            max_bays=20,  # Increased to ensure we have enough bays
            max_rows=10,
            max_tier=5
        )
        # Get the first pile and position created by the block
        self.pile = Pile.objects.filter(block=self.block).first()
        self.position = Position.objects.filter(pile=self.pile).first()
        
        # Create a container visit for testing
        self.iso_type = ISOType.objects.create(
            iso_type='22R1',
            length='20',
            height='HC'
        )
        self.owner = ContainerOwner.objects.create(
            name='Test Shipping Line',
            code='TEST',
            is_shippingline=True
        )
        self.container = ContainerUnit.objects.create(
            iso_type=self.iso_type,
            owner=self.owner,
            container_number='TEST0000001',
            is_active=True,
            is_in_terminal=True
        )
        self.container_visit = ContainerVisit.objects.create(
            category='S',
            container_number=self.container,
            location=self.location
        )

    def test_position_container_assignment(self):
        # Test assigning a container to a position
        self.position.container_visit = self.container_visit
        self.position.save()
        
        # Refresh from database
        self.position.refresh_from_db()
        
        # Verify container is assigned and position is occupied
        self.assertEqual(self.position.container_visit, self.container_visit)
        self.assertTrue(self.position.is_occupied)
        
        # Verify we can't assign the same container to another position
        other_position = Position.objects.filter(pile=self.pile).exclude(pk=self.position.pk).first()
        other_position.container_visit = self.container_visit
        with self.assertRaises(ValidationError):
            other_position.save()

    def test_position_container_removal(self):
        # First assign a container
        self.position.container_visit = self.container_visit
        self.position.save()
        
        # Verify initial state
        self.position.refresh_from_db()
        self.assertEqual(self.position.container_visit, self.container_visit)
        self.assertTrue(self.position.is_occupied)
        
        # Remove the container
        self.position.container_visit = None
        self.position.save()
        
        # Verify container is removed and position is not occupied
        self.position.refresh_from_db()
        self.assertIsNone(self.position.container_visit)
        self.assertFalse(self.position.is_occupied)

    def test_position_list(self):
        url = reverse('minitos_core:position-list', args=[self.location.id, self.block.id, self.pile.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should have positions for all tiers
        self.assertEqual(len(response.data), self.block.max_tier)

    def test_position_detail(self):
        url = reverse('minitos_core:position-detail', args=[self.location.id, self.block.id, self.pile.id, self.position.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tier'], self.position.tier)

    def test_position_allows_in_restriction(self):
        # Get an existing pile and update its allows_in setting
        restricted_pile = Pile.objects.filter(block=self.block, bay=15).first()
        restricted_pile.allows_in = False
        restricted_pile.save()
        
        restricted_position = Position.objects.filter(pile=restricted_pile).first()

        # Try to place a container in a restricted pile
        restricted_position.container_visit = self.container_visit
        with self.assertRaises(ValidationError) as context:
            restricted_position.save()
        self.assertIn("does not allow containers to be placed", str(context.exception))

        # Verify the container wasn't placed
        restricted_position.refresh_from_db()
        self.assertIsNone(restricted_position.container_visit)
        self.assertFalse(restricted_position.is_occupied)

    def test_position_allows_out_restriction(self):
        # Get an existing pile and update its allows_out setting
        restricted_pile = Pile.objects.filter(block=self.block, bay=16).first()
        restricted_pile.allows_out = False
        restricted_pile.save()
        
        restricted_position = Position.objects.filter(pile=restricted_pile).first()

        # Create a 40' container for even bay
        iso_type_40 = ISOType.objects.create(
            iso_type='45R1',
            length='40',
            height='HC'
        )
        container_40 = ContainerUnit.objects.create(
            iso_type=iso_type_40,
            owner=self.owner,
            container_number='TEST0000004',
            is_active=True,
            is_in_terminal=True
        )
        container_visit_40 = ContainerVisit.objects.create(
            category='S',
            container_number=container_40,
            location=self.location
        )

        # Place a container in the restricted pile
        restricted_position.container_visit = container_visit_40
        restricted_position.save()

        # Try to remove the container
        restricted_position.container_visit = None
        with self.assertRaises(ValidationError) as context:
            restricted_position.save()
        self.assertIn("does not allow containers to be removed", str(context.exception))

        # Verify the container wasn't removed
        restricted_position.refresh_from_db()
        self.assertEqual(restricted_position.container_visit, container_visit_40)
        self.assertTrue(restricted_position.is_occupied)

    def test_position_container_removal_with_containers_above(self):
        # Get an existing pile with multiple positions
        pile = Pile.objects.filter(block=self.block, bay=17).first()
        
        # Get positions in tiers 1 and 2
        position1 = Position.objects.filter(pile=pile, tier=1).first()
        position2 = Position.objects.filter(pile=pile, tier=2).first()

        # Create another container for tier 2
        container2 = ContainerUnit.objects.create(
            iso_type=self.iso_type,
            owner=self.owner,
            container_number='TEST0000002',
            is_active=True,
            is_in_terminal=True
        )
        container_visit2 = ContainerVisit.objects.create(
            category='S',
            container_number=container2,
            location=self.location
        )

        # Place containers in both positions
        position1.container_visit = self.container_visit
        position1.save()
        position2.container_visit = container_visit2
        position2.save()

        # Try to remove the bottom container
        position1.container_visit = None
        with self.assertRaises(ValidationError) as context:
            position1.save()
        self.assertIn("there are containers above it", str(context.exception))

        # Verify the container wasn't removed
        position1.refresh_from_db()
        self.assertEqual(position1.container_visit, self.container_visit)
        self.assertTrue(position1.is_occupied)

        # Remove the top container first
        position2.container_visit = None
        position2.save()

        # Now we should be able to remove the bottom container
        position1.container_visit = None
        position1.save()
        position1.refresh_from_db()
        self.assertIsNone(position1.container_visit)
        self.assertFalse(position1.is_occupied)

    def test_position_physical_rules(self):
        # Create a new block which will automatically create piles and positions
        block = Block.objects.create(
            location=self.location,
            name='Block C',
            block_type='AB',
            max_bays=10,
            max_rows=10,
            max_tier=5
        )
        
        # Get a pile from an even-numbered bay (for 40' containers)
        pile = Pile.objects.filter(block=block, bay=2).first()
        position1 = Position.objects.filter(pile=pile, tier=1).first()
        position2 = Position.objects.filter(pile=pile, tier=2).first()
        position3 = Position.objects.filter(pile=pile, tier=3).first()

        # Create two 40' containers for even bay
        iso_type_40 = ISOType.objects.create(
            iso_type='45R1',
            length='40',
            height='HC'
        )
        container_40_1 = ContainerUnit.objects.create(
            iso_type=iso_type_40,
            owner=self.owner,
            container_number='TEST0000005',
            is_active=True,
            is_in_terminal=True
        )
        container_visit_40_1 = ContainerVisit.objects.create(
            category='S',
            container_number=container_40_1,
            location=self.location
        )
        
        container_40_2 = ContainerUnit.objects.create(
            iso_type=iso_type_40,
            owner=self.owner,
            container_number='TEST0000006',
            is_active=True,
            is_in_terminal=True
        )
        container_visit_40_2 = ContainerVisit.objects.create(
            category='S',
            container_number=container_40_2,
            location=self.location
        )

        # Try to place container in tier 2 without tier 1 being occupied
        position2.container_visit = container_visit_40_1
        with self.assertRaises(ValidationError) as context:
            position2.save()
        self.assertIn("when tier 1 is empty", str(context.exception))

        # Place container in tier 1
        position1.container_visit = container_visit_40_1
        position1.save()

        # Now we should be able to place container in tier 2
        position2.container_visit = container_visit_40_2
        position2.save()

        # Try to remove container from tier 1 when tier 2 is occupied
        position1.container_visit = None
        with self.assertRaises(ValidationError) as context:
            position1.save()
        self.assertIn("when there are containers above it", str(context.exception))

        # Remove container from tier 2 first
        position2.container_visit = None
        position2.save()

        # Now we should be able to remove container from tier 1
        position1.container_visit = None
        position1.save()

        # Verify all positions are now empty
        position1.refresh_from_db()
        position2.refresh_from_db()
        self.assertFalse(position1.is_occupied)
        self.assertFalse(position2.is_occupied)

class CheTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.equipment_type = EquipmentType.objects.create(
            name='Test Equipment',
            equipment_type='SC'
        )
        self.che = Che.objects.create(
            name='CHE-001',
            status='IL',
            location=self.location,
            equipment_type=self.equipment_type
        )

    def test_che_list(self):
        url = reverse('minitos_core:che-list', args=[self.location.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_che_detail(self):
        url = reverse('minitos_core:che-detail', args=[self.location.id, self.che.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'CHE-001')

class WorkQueueTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.work_queue = WorkQueue.objects.create(
            name='Test Queue',
            is_active=True,
            location=self.location
        )

    def test_work_queue_list(self):
        url = reverse('minitos_core:work_queue-list', args=[self.location.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_work_queue_detail(self):
        url = reverse('minitos_core:work_queue-detail', args=[self.location.id, self.work_queue.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Test Queue')

class AuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.location = Location.objects.create(
            name='Test Terminal',
            location_type='TL'
        )

    def test_unauthenticated_access(self):
        url = reverse('minitos_core:location-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_authenticated_access(self):
        self.client.force_authenticate(user=self.user)
        url = reverse('minitos_core:location-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

class ModelValidationTests(TestCase):
    def test_container_number_validation(self):
        with self.assertRaises(Exception):
            ContainerUnit.objects.create(
                container_number='INVALID'  # Should be 11 characters
            )

    def test_iso_type_validation(self):
        with self.assertRaises(Exception):
            ISOType.objects.create(
                iso_type='INVALID'  # Should be 4 characters
            )

    def test_container_owner_validation(self):
        with self.assertRaises(Exception):
            ContainerOwner.objects.create(
                code='INVALID'  # Should be 4 characters
            )