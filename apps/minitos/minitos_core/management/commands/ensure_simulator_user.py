import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create or update the non-interactive MiniTOS account used by the simulator.'

    def handle(self, *args, **options):
        username = os.environ.get('SIMULATOR_MINITOS_USERNAME', '').strip()
        password = os.environ.get('SIMULATOR_MINITOS_PASSWORD', '')
        if not username or not password:
            raise CommandError(
                'SIMULATOR_MINITOS_USERNAME and SIMULATOR_MINITOS_PASSWORD are required.'
            )

        user_model = get_user_model()
        with transaction.atomic():
            user, created = user_model.objects.select_for_update().get_or_create(
                username=username,
                defaults={
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                },
            )
            changed_fields = []
            if not user.is_active:
                user.is_active = True
                changed_fields.append('is_active')
            if not user.check_password(password):
                user.set_password(password)
                changed_fields.append('password')
            if changed_fields:
                user.save(update_fields=changed_fields)

        action = 'created' if created else 'updated'
        self.stdout.write(self.style.SUCCESS(f'Simulator user {username!r} {action}.'))
        if user.is_staff or user.is_superuser:
            self.stdout.write(
                self.style.WARNING(
                    f'Simulator user {username!r} has elevated Django privileges.'
                )
            )
