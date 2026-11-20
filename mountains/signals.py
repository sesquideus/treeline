"""
Cache invalidation. Any write to a model the map, the trees or the cached pages are built
from drops every cached payload; see mountains/views/cache.py for why it is all-or-nothing.

Every model of the two apps the pages are drawn from counts, rather than a hand-kept list:
the list pages show ranges, alternative names and country flags, and a list that forgot one
of those would leave the page stale with nothing to say so. Writes are rare enough that an
unnecessary bump costs nothing. Models are matched by app label rather than by class, so a
data migration — which works on historical model classes — invalidates too.

Many-to-many edits (a point's countries) count as writes: the admin saves them after the
instance's own post_save, so a page rendered in between would otherwise be cached without
them.
"""

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from mountains.views.cache import bump_data_version

WATCHED_APPS = {'mountains', 'core'}

#: Derived from the data and rebuilt from it (`mountains.map_zooms`): writing them is not an
#: edit, and counting it as one would ask for the rebuild all over again.
DERIVED_MODELS = {'summitmapzoom', 'mapzoomversion'}


def is_watched(model) -> bool:
    return model._meta.app_label in WATCHED_APPS and model._meta.model_name not in DERIVED_MODELS


class _Bump:
    """One pending bump, shared by every write of a transaction: the first to run does it."""
    done = False

    def __call__(self):
        if not self.done:
            self.done = True
            bump_data_version()


def bump_when_committed():
    """
    Bump the data version once the write is visible to readers: at once outside a
    transaction, at its commit inside one, and only once however many writes it holds.

    Bumping at the write itself was wrong inside a transaction. A reader between the bump and
    the commit still sees the old data, and cached it under the new version, where it stayed
    after the commit; a large import (a changeset of 200 000 summits is one transaction)
    bumped the version 200 000 times, and the map rebuilt its zooms on every request
    meanwhile, from data that had not changed. A rolled-back transaction bumps nothing:
    Django drops its callbacks.

    Every write registers a callback, all sharing one `_Bump` until it has run, so the
    rest cost nothing; registering only the first would lose the bump when that first
    write's savepoint rolls back and a later write stands.
    """
    # On the connection, which is per thread: two threads' transactions never share one.
    connection = transaction.get_connection()
    pending = getattr(connection, 'treeline_pending_bump', None)
    if pending is None or pending.done:
        pending = connection.treeline_pending_bump = _Bump()
    transaction.on_commit(pending)


@receiver(post_save)
@receiver(post_delete)
def invalidate_cached_payloads(sender, **kwargs):
    if is_watched(sender):
        bump_when_committed()


@receiver(m2m_changed)
def invalidate_on_relation_change(sender, instance, action, **kwargs):
    if action.startswith('post_') and is_watched(type(instance)):
        bump_when_committed()
