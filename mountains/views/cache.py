"""
Caching for the flat JSON endpoints.

The map and the tree pages fetch payloads that take a second to build and change only when
the maintainer edits the data — several times a day at most. They are therefore cached
whole, and the whole cache is dropped on any write, by bumping a version counter that every
key is built from: there is no partial invalidation to get wrong, and a stale payload cannot
outlive the next save.

The default backend is LocMemCache, which is per-process. That is enough for one runserver
or one worker; with several workers each keeps its own copy and each rebuilds once per bump.
"""

from django.core.cache import cache

VERSION_KEY = 'mountains:data-version'
TIMEOUT = None          # never expires by itself — a save is what invalidates it


def data_version() -> int:
    """The current version of the geographic data. Every cache key carries it."""
    version = cache.get(VERSION_KEY)
    if version is None:
        version = 1
        cache.set(VERSION_KEY, version, TIMEOUT)
    return version


def bump_data_version() -> int:
    """Invalidate every cached payload. Called from the post_save/post_delete signals."""
    try:
        return cache.incr(VERSION_KEY)
    except ValueError:      # nothing cached yet, so nothing to invalidate
        cache.set(VERSION_KEY, 1, TIMEOUT)
        return 1


def cached_json(name: str, build):
    """
    Return the payload for `name`, building it with `build()` on a miss.

    `build` must return something JSON-serializable; it is the built value that is cached,
    not the response, so a view stays free to wrap it however it likes.
    """
    key = f'{name}:v{data_version()}'
    payload = cache.get(key)
    if payload is None:
        payload = build()
        cache.set(key, payload, TIMEOUT)
    return payload
