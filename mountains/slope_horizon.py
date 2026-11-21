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
    'slope': ('slope_parent', 'slope_computed', lambda rows, keys: slope_parents(rows, keys)),
    'horizon': ('horizon_parent', 'horizon_computed', lambda rows, keys: horizon_parents(rows, keys, 0.0)),
    'horizon_std': ('horizon_parent_std', 'horizon_std_computed',
                    lambda rows, keys: horizon_parents(rows, keys, 0.14)),
}


def compute(kind, summits=None):
    """Work out and store one kind of parent for `summits` (default: all). Returns the pks that changed."""
    parent_field, stamp_field, method = KINDS[kind]
    rows = [(pk, loc.y, loc.x, alt) for pk, loc, alt in
            Summit.objects.filter(point__location__isnull=False)
            .values_list('pk', 'point__location', 'point__altitude')]
    keys = None if summits is None else list(summits.values_list('pk', flat=True))
    result = method(rows, keys)
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
        Summit.objects.bulk_update(todo, [parent_field, stamp_field], batch_size=2000)
        # bulk_update sends no save signals: the cached pages and the map are told here.
        transaction.on_commit(bump_data_version)
    return changed
