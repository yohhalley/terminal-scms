from django.apps import AppConfig


class MinitosCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'minitos_core'

    def ready(self):
        import minitos_core.signals  # noqa
