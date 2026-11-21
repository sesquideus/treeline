"""
Slope and horizon parents of every summit (or those given), by branch and bound
(`mountains.slope_horizon`): seconds where the exhaustive admin actions took days.

    uv run python manage.py compute_slope_horizon                    # all three, every summit
    uv run python manage.py compute_slope_horizon --kind slope --pk 62 63
"""
import time

from django.core.management.base import BaseCommand

from mountains.models import Summit
from mountains.slope_horizon import KINDS, compute


class Command(BaseCommand):
    help = 'Compute slope and horizon parents (all summits are candidates).'

    def add_arguments(self, parser):
        parser.add_argument('--kind', choices=list(KINDS), action='append',
                            help='which parent (default: all three); may be repeated')
        parser.add_argument('--pk', type=int, nargs='+', help='only these summits\' parents')

    def handle(self, kind=None, pk=None, **options):
        summits = Summit.objects.filter(pk__in=pk) if pk else None
        for k in kind or list(KINDS):
            start = time.time()
            changed = compute(k, summits)
            self.stdout.write(self.style.SUCCESS(f'{k}: {len(changed)} changed in {time.time() - start:.0f} s'))
