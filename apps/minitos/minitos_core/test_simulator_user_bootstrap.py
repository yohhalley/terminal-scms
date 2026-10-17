import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class SimulatorUserBootstrapTests(TestCase):
    def run_command(self, username='simulator', password='initial-secret'):
        output = StringIO()
        with patch.dict(
            os.environ,
            {
                'SIMULATOR_MINITOS_USERNAME': username,
                'SIMULATOR_MINITOS_PASSWORD': password,
            },
        ):
            call_command('ensure_simulator_user', stdout=output)
        return output.getvalue()

    def test_creates_active_unprivileged_simulator_user(self):
        output = self.run_command()

        user = get_user_model().objects.get(username='simulator')
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password('initial-secret'))
        self.assertIn("Simulator user 'simulator' created.", output)

    def test_updates_existing_simulator_password_idempotently(self):
        user = get_user_model().objects.create_user(
            username='simulator',
            password='old-secret',
            is_active=False,
        )

        self.run_command(password='new-secret')
        user.refresh_from_db()

        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password('new-secret'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_requires_both_credentials(self):
        with patch.dict(
            os.environ,
            {
                'SIMULATOR_MINITOS_USERNAME': 'simulator',
                'SIMULATOR_MINITOS_PASSWORD': '',
            },
        ):
            with self.assertRaises(CommandError):
                call_command('ensure_simulator_user')
