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

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from mountains.views.cache import bump_data_version

WATCHED_APPS = {'mountains', 'core'}


def is_watched(model) -> bool:
    return model._meta.app_label in WATCHED_APPS


@receiver(post_save)
@receiver(post_delete)
def invalidate_cached_payloads(sender, **kwargs):
    if is_watched(sender):
        bump_data_version()


@receiver(m2m_changed)
def invalidate_on_relation_change(sender, instance, action, **kwargs):
    if action.startswith('post_') and is_watched(type(instance)):
        bump_data_version()
