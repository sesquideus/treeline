from django.http import JsonResponse

from mountains.views.tree.tree import TreeView

from mountains.models import Summit


class SummitTreeView(TreeView):
    def get_queryset(self):
        # `with_countries()`: to_dict() serializes the flags, and without the prefetch that is
        # one query per node.
        return (Summit.objects
                .with_prominence().with_isolation().with_slope_parent().with_horizon_parent()
                .with_countries())


class ProminenceJsonView(SummitTreeView):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'tree': self.build_tree(list(self.get_queryset()), 'prominence_parent_id')
        })


class IsolationJsonView(SummitTreeView):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'tree': self.build_tree(list(self.get_queryset()), 'isolation_parent_id')
        })


class SlopeJsonView(SummitTreeView):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'tree': self.build_tree(list(self.get_queryset()), 'slope_parent_id')
        })


class HorizonJsonView(SummitTreeView):
    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'tree': self.build_tree(list(self.get_queryset()), 'horizon_parent_id')
        })

