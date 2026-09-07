from django.apps import AppConfig


class MountainsConfig(AppConfig):
    name = 'mountains'

    def ready(self):
        from mountains import signals    # noqa: F401 — registers the cache invalidation
