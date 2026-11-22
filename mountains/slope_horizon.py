"""
Slope and horizon parents for the database's summits (`core.functions.parents`): what the
admin actions and `manage.py compute_slope_horizon` both call, so the two never disagree.

Every summit is a candidate parent; `summits` (a queryset) says whose parents are worked out.
Each gets its `*_computed` stamp either way: a null parent then means "looked, there is none"
rather than "nobody looked", which the map draws differently.
"""
from django.db import transaction
from django.utils import timezone

from core.functions.parents import horizon_parents, slope_parents
from mountains.models import Summit
from mountains.views.cache import bump_data_version

#: kind: (parent field, stamp field, how to compute)
KINDS = {
    'slope': ('slope_parent', 'slope_computed', lambda rows, keys, p: slope_parents(rows, keys, p)),
    'horizon': ('horizon_parent', 'horizon_computed', lambda rows, keys, p: horizon_parents(rows, keys, 0.0, p)),
    'horizon_std': ('horizon_parent_std', 'horizon_std_computed',
                    lambda rows, keys, p: horizon_parents(rows, keys, 0.14, p)),
}


def compute(kind, summits=None, progress=None, say=None):
    """
    Work out and store one kind of parent for `summits` (default: all). Returns the pks that
    changed. `progress(stage, done, total)` is called as the search and then the storing go
    ('search', 'store'), `say(text)` at each phase.
    """
    say = say or (lambda text: None)
    progress = progress or (lambda stage, done, total: None)
    parent_field, stamp_field, method = KINDS[kind]
    rows = [(pk, loc.y, loc.x, alt) for pk, loc, alt in
            Summit.objects.filter(point__location__isnull=False)
            .values_list('pk', 'point__location', 'point__altitude')]
    keys = None if summits is None else list(summits.values_list('pk', flat=True))
    say(f'{len(rows)} summits loaded; building the tree')
    result = method(rows, keys, lambda done, total: progress('search', done, total))
    now = timezone.now()
    targets = Summit.objects.filter(pk__in=list(result)).only('pk', parent_field, stamp_field)
    changed, todo = [], []
    for summit in targets:
        parent = result[summit.pk][0]
        if getattr(summit, f'{parent_field}_id') != parent:
            changed.append(summit.pk)
        setattr(summit, f'{parent_field}_id', parent)
        setattr(summit, stamp_field, now)
        todo.append(summit)
    with transaction.atomic():
        chunk = 10000
        progress('store', 0, len(todo))
        for at in range(0, len(todo), chunk):
            Summit.objects.bulk_update(todo[at:at + chunk], [parent_field, stamp_field], batch_size=2000)
            progress('store', min(at + chunk, len(todo)), len(todo))
        # bulk_update sends no save signals: the cached pages and the map are told here.
        transaction.on_commit(bump_data_version)
    return changed
