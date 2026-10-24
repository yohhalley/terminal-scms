import json

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from minitos_core.models import (
    Che,
    Enterprise,
    EquipmentType,
    IntegrationOutbox,
    LayoutVersion,
    Location,
    Site,
    SiteConfigurationSnapshot,
)
from minitos_core.services import ensure_site_for_location, publish_layout
from minitos_core.services.site_sync import snapshot_checksum


class SiteIdentityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='site-user', password='test-password')
        self.client.force_login(self.user)

    def test_terminal_editor_creation_atomically_creates_site_and_outbox_event(self):
        response = self.client.post(
            reverse('minitos_core:location_api_create'),
            data=json.dumps({
                'name': 'New Terminal',
                'location_type': Location.TL,
                'latitude': 41.35,
                'longitude': 2.17,
                'timezone': 'Europe/Madrid',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        site = Site.objects.select_related('enterprise', 'location').get(
            location_id=response.json()['id'],
        )
        self.assertEqual(site.enterprise.code, 'terminal-velocity')
        self.assertEqual(site.code, 'new-terminal')
        event = IntegrationOutbox.objects.get(schema='scms.site.created.v1')
        self.assertEqual(event.payload['site']['id'], str(site.pk))
        self.assertEqual(event.topic, 'scms/v2/terminal-velocity/new-terminal/tos/site/created')

    def test_site_rejects_transport_location(self):
        enterprise = Enterprise.objects.create(code='test-enterprise', name='Test Enterprise')
        vessel = Location.objects.create(name='Test Vessel', location_type=Location.VL)
        site = Site(enterprise=enterprise, location=vessel, code='test-vessel')

        with self.assertRaises(ValidationError):
            site.full_clean()

    def test_site_code_is_immutable_through_api(self):
        location = Location.objects.create(name='Stable Terminal', location_type=Location.TL)
        site, _ = ensure_site_for_location(location)
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.patch(
            reverse('minitos_core:site-detail', args=[site.pk]),
            {'code': 'changed'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        site.refresh_from_db()
        self.assertEqual(site.code, 'stable-terminal')


@override_settings(OES_API_TOKEN='oes-secret')
class SiteConfigurationPublicationTests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            name='Published Terminal',
            location_type=Location.TL,
            latitude=41.35,
            longitude=2.17,
            map_origin_latitude=41.35,
            map_origin_longitude=2.17,
            timezone='Europe/Madrid',
        )
        self.site, _ = ensure_site_for_location(self.location)
        self.layout = LayoutVersion.objects.create(
            location=self.location,
            version=1,
            name='Execution layout',
            is_current=True,
        )
        equipment_type = EquipmentType.objects.create(
            name='Snapshot Tractor',
            equipment_type=EquipmentType.TT,
            is_horizontal_transport=True,
        )
        self.che = Che.objects.create(
            name='TT-SNAPSHOT',
            equipment_type=equipment_type,
            location=self.location,
            latitude=41.3501,
            longitude=2.1701,
            powered_on=True,
            is_available=True,
        )

    def test_publish_creates_immutable_snapshot_and_configuration_event(self):
        publish_layout(self.layout)

        self.site.refresh_from_db()
        snapshot = SiteConfigurationSnapshot.objects.get(site=self.site, revision=1)
        self.assertEqual(self.site.lifecycle_status, Site.PUBLISHED)
        self.assertEqual(self.site.configuration_revision, 1)
        self.assertEqual(snapshot.checksum, snapshot_checksum(snapshot.payload))
        self.assertEqual(snapshot.payload['site']['id'], str(self.site.pk))
        self.assertEqual(snapshot.payload['equipment']['ches'][0]['id'], self.che.pk)
        event = IntegrationOutbox.objects.get(
            schema='scms.site.configuration.published.v1',
        )
        self.assertEqual(event.payload['data']['configuration_revision'], 1)
        self.assertEqual(event.payload['data']['snapshot_checksum'], snapshot.checksum)

        publish_layout(self.layout)
        self.site.refresh_from_db()
        self.assertEqual(self.site.configuration_revision, 2)
        self.assertEqual(
            list(self.site.configuration_snapshots.values_list('revision', flat=True)),
            [2, 1],
        )

    def test_snapshot_endpoint_requires_operator_or_oes_token(self):
        publish_layout(self.layout)
        url = reverse(
            'minitos_core:site-execution-snapshot',
            args=[self.site.pk],
        )
        api = APIClient()

        denied = api.get(url, {'revision': 1})
        allowed = api.get(url, {'revision': 1}, HTTP_X_OES_TOKEN='oes-secret')

        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed.data['revision'], 1)
        self.assertEqual(allowed.data['payload']['site']['id'], str(self.site.pk))
