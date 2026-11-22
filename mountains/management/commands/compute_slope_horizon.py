"""
Slope and horizon parents of every summit (or those given), by branch and bound
(`mountains.slope_horizon`): seconds where the exhaustive admin actions took days.

    uv run python manage.py compute_slope_horizon                    # all three, every summit
    uv run python manage.py compute_slope_horizon --kind slope --pk 62 63

A progress bar for the search and one for storing (tqdm, on stderr).
"""
import time

from django.core.management.base import BaseCommand
from tqdm import tqdm

from mountains.models import Summit
from mountains.slope_horizon import KINDS, compute

STAGES = {'search': 'searching', 'store': 'storing'}


class Command(BaseCommand):
    help = 'Compute slope and horizon parents (all summits are candidates).'

    def add_arguments(self, parser):
        parser.add_argument('--kind', choices=list(KINDS), action='append',
                            help='which parent (default: all three); may be repeated')
        parser.add_argument('--pk', type=int, nargs='+', help='only these summits\' parents')

    def handle(self, kind=None, pk=None, **options):
        summits = Summit.objects.filter(pk__in=pk) if pk else None
        width = max(len(k) for k in KINDS) + 1 + max(len(v) for v in STAGES.values())
        for k in kind or list(KINDS):
            start = time.time()
            bars = {}

            def progress(stage, done, total, k=k):
                bar = bars.get(stage)
                if bar is None:
                    digits = len(str(total))
                    bar = bars[stage] = tqdm(
                        total=total, desc=f'{k} {STAGES[stage]}'.ljust(width), unit='summit', leave=True,
                        bar_format=f'{{desc}} {{percentage:5.1f}} % |{{bar}}| {{n:{digits}d}}/{{total:{digits}d}} '
                                   f'[{{elapsed}} < {{remaining}}]')
                bar.update(done - bar.n)
                if done >= total:
                    bar.close()

            changed = compute(k, summits, progress=progress,
                              say=lambda text, k=k: tqdm.write(f'{k}: {text}'))
            for bar in bars.values():
                bar.close()
            self.stdout.write(self.style.SUCCESS(f'{k}: {len(changed)} changed in {time.time() - start:.0f} s'))
