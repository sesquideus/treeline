from django.http import JsonResponse
from django.views import View

from mountains.views.cache import cached_json


class CachedJsonMixin:
    """
    A JSON view whose payload depends on the data and nothing else — no query string, no
    user. Such a payload is built once and served from the cache until the next write to
    the geographic models; see mountains/views/cache.py.

    Subclasses implement `build_payload()` instead of `get()`. The cache name defaults to
    the view's dotted path, since several modules here define a `GeoJsonView`.
    """

    def build_payload(self):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        cls = type(self)
        return JsonResponse(cached_json(f'{cls.__module__}.{cls.__qualname__}',
                                        self.build_payload))


class TreeView(View):
    @staticmethod
    def build_tree(summits, parent_attr):
        by_pk = {s.pk: {**s.to_dict(), 'children': []} for s in summits}
        roots = []
        for s in summits:
            parent_pk = getattr(s, parent_attr)
            if parent_pk and parent_pk in by_pk:
                by_pk[parent_pk]['children'].append(by_pk[s.pk])
            else:
                roots.append(by_pk[s.pk])
        return roots


class FlatJsonView(CachedJsonMixin, TreeView):
    object_name = 'objects'

    def build_payload(self):
        return {self.object_name: [o.to_dict() for o in self.get_queryset()]}


class TreeJsonView(CachedJsonMixin, TreeView):
    def build_payload(self):
        return {'tree': self.build_tree(list(self.get_queryset()), 'prominence_parent_id')}


class FlatGeoJsonView(CachedJsonMixin, View):
    def build_payload(self):
        return {
            'type': 'FeatureCollection',
            'features': [
                f for o in self.get_queryset()
                if (f := o.to_geojson()) is not None
            ],
        }
