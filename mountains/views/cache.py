"""
Caching for the flat JSON endpoints and the slow HTML pages.

The map and the tree pages fetch payloads that take a second to build, and the list and
forest pages take one to two and a half seconds to render — almost all of it template time
over a couple of thousand rows, not SQL. All of them change only when the maintainer edits
the data, several times a day at most. They are therefore cached whole, and the whole cache
is dropped on any write, by bumping a version counter that every key is built from: there
is no partial invalidation to get wrong, and a stale page cannot outlive the next save.

The backend is on disk (see CACHES in settings) so that every worker reads the same version
counter. A per-process cache let a save invalidate one worker while the other kept serving
the old data.

Every key also carries `BOOT`, minted once per process start. A disk cache survives a
restart, and a deploy changes templates and serializers without any save happening; without
this, pages rendered by the old code would be served after the new code went live. uwsgi
imports the application in the master before forking, so its workers share one token.
"""

import secrets
import zlib

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

VERSION_KEY = 'mountains:data-version'
TIMEOUT = None          # never expires by itself — a save is what invalidates it
BOOT = secrets.token_hex(4)


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


def versioned_key(name: str) -> str:
    return f'{name}:v{data_version()}:{BOOT}'


def cached_json(name: str, build):
    """
    Return the payload for `name`, building it with `build()` on a miss.

    `build` must return something JSON-serializable; it is the built value that is cached,
    not the response, so a view stays free to wrap it however it likes.
    """
    key = versioned_key(name)
    payload = cache.get(key)
    if payload is None:
        payload = build()
        cache.set(key, payload, TIMEOUT)
    return payload


class CachedPageMixin:
    """
    Serve a rendered page from the cache, keyed on its full URL and the data version.

    For views whose output depends on the data and the query string and nothing else. The
    query string is part of the key because the list pages sort and filter by it; robots.txt
    keeps crawlers out of those variants, and MAX_ENTRIES bounds what anyone else can add.

    Only anonymous GETs and HEADs are cached. Nothing on these pages differs for a signed-in
    user today, but the maintainer is the one who edits the data and should always see a
    fresh render — and a page that someday does vary by user must not leak through here.
    Only a complete 200 is stored: an error is not worth keeping, and must not stick.

    The body is stored zlib-compressed — 4.6 MB of table compresses to about 400 KB — and
    decompressed on the way out, in ~5 ms, so GZipMiddleware still negotiates encoding as
    it does for every other response.
    """

    def dispatch(self, request, *args, **kwargs):
        if not self.page_is_cacheable(request):
            return super().dispatch(request, *args, **kwargs)
        key = versioned_key(f'page:{request.get_full_path()}')
        if (stored := cache.get(key)) is not None:
            content_type, body = stored
            return HttpResponse(zlib.decompress(body), content_type=content_type)
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code == 200 and not response.streaming:
            if hasattr(response, 'render'):
                response.render()
            cache.set(key, (response['Content-Type'], zlib.compress(response.content)),
                      TIMEOUT)
        return response

    @staticmethod
    def page_is_cacheable(request) -> bool:
        return (settings.PAGE_CACHE
                and request.method in ('GET', 'HEAD')
                and not request.user.is_authenticated)
