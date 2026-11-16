"""
Recompute `NamedPoint.altitude_egm2008` from each point's altitude and datum.

    uv run python manage.py recompute_altitudes                 # every point with a datum
    uv run python manage.py recompute_altitudes --datum Bpv
    uv run python manage.py recompute_altitudes --only-missing

Run it after the migration that introduced datums, after fetching new PROJ grids, and after
bulk edits that bypass `save()`. Points without a datum or a location get null. Prints the
shift (EGM2008 − raw) per datum, so an implausible one — a grid gone missing, a datum
assigned to the wrong country — shows up at once.
"""
import statistics

from django.core.management.base import BaseCommand, CommandError

from core.models import VerticalDatum
from mountains.models import NamedPoint


class Command(BaseCommand):
    help = 'Recompute EGM2008 altitudes of named points'

    def add_arguments(self, parser):
        parser.add_argument('--datum', help='only points in this datum (its code)')
        parser.add_argument('--only-missing', action='store_true',
                            help='only points whose EGM2008 altitude is not set')

    def handle(self, *args, datum=None, only_missing=False, **options):
        points = NamedPoint.objects.select_related('altitude_datum').order_by('pk')
        if datum:
            if not VerticalDatum.objects.filter(code=datum).exists():
                raise CommandError(f'No datum {datum!r}; known: '
                                   + ', '.join(VerticalDatum.objects.values_list('code', flat=True)))
            points = points.filter(altitude_datum__code=datum)
        if only_missing:
            points = points.filter(altitude_egm2008=None)

        changed, shifts, failed = [], {}, {}
        for point in points.iterator(chunk_size=1000):
            value = point.compute_altitude_egm2008()
            code = point.altitude_datum.code if point.altitude_datum_id else '(unknown)'
            if value is None:
                if point.altitude_datum_id is not None:
                    failed[code] = failed.get(code, 0) + 1
            else:
                shifts.setdefault(code, []).append(value - point.altitude)
            if value != point.altitude_egm2008:
                point.altitude_egm2008 = value
                changed.append(point)
        NamedPoint.objects.bulk_update(changed, ['altitude_egm2008'], batch_size=1000)

        for code, values in sorted(shifts.items()):
            self.stdout.write(f'{code:12} {len(values):6} points   shift min {min(values):+.3f}'
                              f'  median {statistics.median(values):+.3f}  max {max(values):+.3f} m')
        for code, count in sorted(failed.items()):
            self.stdout.write(self.style.WARNING(
                f'{code:12} {count:6} points could not be converted (missing grid or outside it)'))
        unknown = points.filter(altitude_datum=None).count()
        if unknown:
            self.stdout.write(f'{"(unknown)":12} {unknown:6} points without a datum')
        self.stdout.write(self.style.SUCCESS(f'{len(changed)} points updated'))
