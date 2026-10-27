"""TCL-032: PositionSerializer.partial_update referenced the undefined name ``Null``.

DRF never calls a serializer method named ``partial_update`` (ModelViewSet
PATCH goes through ``serializer.save()`` -> ``update()``), so this path is only
reachable by calling it directly, which is what these tests do.
"""

from minitos_core.models import ContainerVisit, Pile, Position
from minitos_core.serializers import PositionSerializer
from minitos_core.services import sync_yard_grid
from minitos_core.tests import BaseTestCase


class PositionSerializerPartialUpdateTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        block = self.create_storage_grid(
            location=self.location, name="TCL-032 Grid", block_type="AB",
            max_bays=2, max_rows=2, max_tier=3,
        )
        sync_yard_grid(block)
        pile = Pile.objects.filter(storage_grid=block).first()
        self.position = Position.objects.filter(pile=pile).order_by("tier").first()

    def test_partial_update_applies_validated_data_without_raising(self):
        result = PositionSerializer().partial_update(self.position, {"is_reserved": True})
        self.assertIsInstance(result, Position)
        self.assertEqual(result.pk, self.position.pk)
        self.position.refresh_from_db()
        self.assertTrue(self.position.is_reserved)

    def test_partial_update_with_a_container_visit_assigned_does_not_raise(self):
        container_visit = ContainerVisit.objects.create(location=self.location)
        Position.objects.filter(pk=self.position.pk).update(container_visit=container_visit)
        result = PositionSerializer().partial_update(self.position, {"is_reserved": False})
        self.assertEqual(result.pk, self.position.pk)
