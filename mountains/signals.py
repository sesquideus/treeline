"""
Cache invalidation. Any write to a model the map or the trees are built from drops every
cached payload; see mountains/views/cache.py for why it is all-or-nothing.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from mountains.models import Col, Confluence, NamedPoint, River, Summit
from mountains.views.cache import bump_data_version

WATCHED = (NamedPoint, Summit, Col, River, Confluence)


@receiver(post_save)
@receiver(post_delete)
def invalidate_cached_payloads(sender, **kwargs):
    if sender in WATCHED:
        bump_data_version()
