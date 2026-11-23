"""
Rebuild the global map's zooms and edges (`mountains.map_zooms`): the only way they are rebuilt.
Run it after an import or a batch of them (`apply_lidar_changeset` does not, unless given
--refresh-map), after edits, and after `migrate` on a deploy; until then the map shows the table
as last built.
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
