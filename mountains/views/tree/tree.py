from django.http import JsonResponse
from django.views import View


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


class FlatJsonView(TreeView):
    object_name = 'objects'

    def get(self, request, *args, **kwargs):
        return JsonResponse({
            self.object_name: [o.to_dict() for o in self.get_queryset()],
        })


class TreeJsonView(TreeView):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'tree': self.build_tree(list(self.get_queryset()), 'prominence_parent_id')
        })


class FlatGeoJsonView(View):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': [
                f for o in self.get_queryset()
                if (f := o.to_geojson()) is not None
            ],
        })