"""
Rebuild the global map's zooms and edges now (`mountains.map_zooms`), rather than on the first
map request after a change: run it after a large import, and after `migrate` on a deploy.
"""
import time

from django.core.management.base import BaseCommand

from mountains.map_zooms import refresh_and_record_map_zooms


class Command(BaseCommand):
    help = 'Rebuild the zoom levels and edges the global map loads by.'

    def handle(self, **options):
        start = time.time()
        n = refresh_and_record_map_zooms()
        self.stdout.write(self.style.SUCCESS(f'{n} summits in {time.time() - start:.1f} s'))
