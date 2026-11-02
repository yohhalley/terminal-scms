"""TCL-003b contract test: scms.visit.status_changed.v1 payloads vs the schema.

Drives real status changes (seed, transition APIs, model saves, an OES
execution message) and validates every queued outbox payload against
contracts/schemas/scms.visit.status_changed.v1.schema.json, and checks that the
schema's enums still match the models.

The schema lives outside the app directory, so in an app-only Docker image the
test skips; under SCMS_CONTRACT_STRICT=1 a missing schema is a failure.
"""

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from minitos_core.models import CarrierVisit, ContainerVisit, IntegrationOutbox, Order
from minitos_core.scenarios import reset_scenario
from minitos_core.services import integration_events, status_events

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / 'contracts' / 'schemas'
    / 'scms.visit.status_changed.v1.schema.json'
)
TOPIC = re.compile(
    r'^scms/v2/(?P<enterprise>[^/]+)/(?P<site>[^/]+)/tos/'
    r'(?P<kind>carrier-visits|container-visits)/(?P<entity_id>\d+)/status-changed$'
)
KIND_FOR_ENTITY = {'carrier_visit': 'carrier-visits', 'container_visit': 'container-visits'}
TYPES = {
    'object': dict, 'string': str, 'integer': int, 'array': list, 'boolean': bool, 'null': type(None),
}


def check_schema(instance, schema, path='$'):
    """Stdlib JSON-Schema subset used by this schema: type, const, enum, required,
    properties, additionalProperties: false, allOf/if/then, not/required and
    format uuid/date-time. Unknown keywords would be ignored, so the schema
    only uses these (asserted below)."""
    errors = []
    expected = schema.get('type')
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(instance, TYPES[name]) and not (name == 'integer' and isinstance(instance, bool))
            for name in allowed
        ):
            return [f'{path}: expected {expected}, got {type(instance).__name__}']
    if 'const' in schema and instance != schema['const']:
        errors.append(f'{path}: expected const {schema["const"]!r}, got {instance!r}')
    if 'enum' in schema and instance not in schema['enum']:
        errors.append(f'{path}: {instance!r} not in enum')
    fmt = schema.get('format')
    if fmt and isinstance(instance, str):
        try:
            if fmt == 'uuid':
                uuid.UUID(instance)
            elif fmt == 'date-time' and datetime.fromisoformat(instance).tzinfo is None:
                errors.append(f'{path}: date-time without a UTC offset')
        except ValueError:
            errors.append(f'{path}: {instance!r} is not a {fmt}')
    if isinstance(instance, dict):
        for key in schema.get('required', []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        properties = schema.get('properties', {})
        for key, child in properties.items():
            if key in instance:
                errors.extend(check_schema(instance[key], child, f'{path}.{key}'))
        if schema.get('additionalProperties') is False:
            errors.extend(f"{path}: unexpected property '{key}'" for key in instance if key not in properties)
    if 'not' in schema and not check_schema(instance, schema['not'], path):
        errors.append(f'{path}: matches a forbidden shape {schema["not"]}')
    for branch in schema.get('allOf', []):
        if 'if' in branch:
            if not check_schema(instance, branch['if'], path) and 'then' in branch:
                errors.extend(check_schema(instance, branch['then'], path))
        else:
            errors.extend(check_schema(instance, branch, path))
    return errors


SUPPORTED_KEYWORDS = {
    '$schema', '$id', 'title', 'description', 'type', 'const', 'enum', 'required', 'properties',
    'additionalProperties', 'allOf', 'if', 'then', 'not', 'format',
}


def keywords(schema, found=None):
    found = set() if found is None else found
    if isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            if key == 'properties':
                for child in value.values():
                    keywords(child, found)
            elif key in {'allOf'}:
                for child in value:
                    keywords(child, found)
            elif key in {'if', 'then', 'not', 'additionalProperties'} and isinstance(value, dict):
                keywords(value, found)
    return found


class VisitStatusChangedContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not SCHEMA_PATH.exists():
            if os.environ.get('SCMS_CONTRACT_STRICT'):
                raise AssertionError(f'{SCHEMA_PATH} is missing (strict mode)')
            cls.schema = None
        else:
            cls.schema = json.loads(SCHEMA_PATH.read_text())

    def setUp(self):
        if self.schema is None:
            self.skipTest(f'{SCHEMA_PATH} is not available (app-only Docker image)')

    def produce_events(self):
        """Every status-change path we have, through the real code."""
        with self.captureOnCommitCallbacks(execute=True):
            reset_scenario('import_v1', containers=2)
        visit = CarrierVisit.objects.get()
        container_visits = list(ContainerVisit.objects.order_by('pk'))
        user = get_user_model().objects.create_superuser(
            'tcl003b-contract', 'contract@terminal-scms.invalid', 'test-password',
        )
        client = APIClient()
        client.force_authenticate(user=user)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(
                reverse('minitos_core:transition_visit_status', args=[visit.pk]),
                {'status': 'InOperation'}, format='json',
            )
        self.assertEqual(response.status_code, 200, response.content)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(
                reverse('minitos_core:transition_container_status', args=[container_visits[1].pk]),
                {'status': 'Arrived'}, format='json',
            )
        self.assertEqual(response.status_code, 200, response.content)

        def fake_apply(order_id, event, **kwargs):
            moving = ContainerVisit.objects.get(pk=container_visits[0].pk)
            moving.status = ContainerVisit.ON_EQUIPMENT
            moving.save(update_fields=['status'])
            return Order.objects.get(pk=order_id)

        message = {
            'schema': 'scms.execution.event.v1', 'event_id': str(uuid.uuid4()),
            'terminal': {'id': visit.location_id}, 'source_sim_time_ms': 5000,
            'data': {'order_id': Order.objects.order_by('pk').first().pk, 'event': 'order_started'},
        }
        with (
            mock.patch.object(integration_events, 'apply_execution_event', side_effect=fake_apply),
            self.captureOnCommitCallbacks(execute=True),
        ):
            integration_events.apply_execution_message('oes/topic', json.dumps(message))
        with self.captureOnCommitCallbacks(execute=True):
            for new_status in ('Complete', 'Departed'):
                visit.status = new_status
                visit.save()
        return list(IntegrationOutbox.objects.filter(schema=status_events.SCHEMA).order_by('created_at', 'pk'))

    def test_produced_payloads_validate_against_the_schema(self):
        rows = self.produce_events()
        self.assertEqual(len(rows), 8)  # 3 created, 1 vessel API, 1 container API, 1 OES, 2 vessel saves
        self.assertEqual(
            {row.payload['data']['source'] for row in rows},
            {'minitos.scenario', 'minitos.api', 'oes.execution', 'minitos.system'},
        )
        errors = []
        for row in rows:
            data = row.payload['data']
            label = f'{data["entity_type"]} {data["from_status"]}->{data["to_status"]}'
            errors.extend(f'{label}: {error}' for error in check_schema(row.payload, self.schema))
            match = TOPIC.match(row.topic)
            if match is None:
                errors.append(f'{label}: topic {row.topic!r} does not match the contract')
                continue
            if match['kind'] != KIND_FOR_ENTITY[data['entity_type']] or int(match['entity_id']) != data['entity_id']:
                errors.append(f'{label}: topic {row.topic!r} does not name the entity')
            if (match['enterprise'], match['site']) != (row.payload['enterprise']['code'], row.payload['site']['code']):
                errors.append(f'{label}: topic root does not match the envelope')
            if (row.qos, row.retain) != (1, False):
                errors.append(f'{label}: QoS/retain {(row.qos, row.retain)}')
        self.assertEqual(errors, [])

    def test_schema_rejects_broken_payloads(self):
        good = self.produce_events()[0].payload
        self.assertEqual(check_schema(good, self.schema), [])
        broken = {
            'missing to_status': lambda p: p['data'].pop('to_status'),
            'unknown source': lambda p: p['data'].__setitem__('source', 'somewhere'),
            'extra data key': lambda p: p['data'].__setitem__('note', 'x'),
            'string sim time': lambda p: p['data'].__setitem__('sim_time_ms', '5000'),
            'naive occurred_at': lambda p: p.__setitem__('occurred_at', '2026-11-02T08:00:00'),
            'wrong details block': lambda p: p['data'].__setitem__(
                'container_visit', {'container_number': None, 'category': 'I', 'ib_carrier': None, 'ob_carrier': None},
            ),
            'container status on a vessel visit': lambda p: p['data'].__setitem__('to_status', 'AtYard'),
        }
        for name, mutate in broken.items():
            payload = json.loads(json.dumps(good))
            mutate(payload)
            self.assertNotEqual(check_schema(payload, self.schema), [], name)

    def test_schema_matches_the_models(self):
        branches = {
            branch['if']['properties']['entity_type']['const']: branch['then']['properties']
            for branch in self.schema['properties']['data']['allOf']
        }
        carrier = [value for value, _ in CarrierVisit.STATUS_CHOICES]
        container = [value for value, _ in ContainerVisit.STATUS_CHOICES]
        self.assertEqual(branches['carrier_visit']['to_status']['enum'], carrier)
        self.assertEqual(branches['carrier_visit']['from_status']['enum'], carrier + [None])
        self.assertEqual(branches['container_visit']['to_status']['enum'], container)
        self.assertEqual(branches['container_visit']['from_status']['enum'], container + [None])
        data = self.schema['properties']['data']['properties']
        self.assertEqual(data['source']['enum'], list(status_events.SOURCES))
        self.assertEqual(
            data['carrier_visit']['properties']['visit_type']['enum'],
            [value for value, _ in CarrierVisit._meta.get_field('visit_type').choices],
        )
        self.assertEqual(
            data['container_visit']['properties']['category']['enum'],
            [value for value, _ in ContainerVisit.CATEGORIES],
        )
        self.assertEqual(self.schema['properties']['schema']['const'], status_events.SCHEMA)

    def test_schema_uses_only_keywords_the_validator_checks(self):
        self.assertEqual(keywords(self.schema) - SUPPORTED_KEYWORDS, set())
