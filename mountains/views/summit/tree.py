from mountains.views.tree.tree import CachedJsonMixin, TreeView

from mountains.models import Summit


class SummitTreeView(CachedJsonMixin, TreeView):
    def get_queryset(self):
        # `with_countries()`: to_dict() serializes the flags, and without the prefetch that is
        # one query per node.
        return (Summit.objects
                .with_prominence().with_isolation().with_slope_parent().with_horizon_parent()
                .with_countries())


class ProminenceJsonView(SummitTreeView):
    def build_payload(self):
        return {'tree': self.build_tree(list(self.get_queryset()), 'prominence_parent_id')}


class IsolationJsonView(SummitTreeView):
    def build_payload(self):
        return {'tree': self.build_tree(list(self.get_queryset()), 'isolation_parent_id')}


class SlopeJsonView(SummitTreeView):
    def build_payload(self):
        return {'tree': self.build_tree(list(self.get_queryset()), 'slope_parent_id')}


class HorizonJsonView(SummitTreeView):
    def build_payload(self):
        return {'tree': self.build_tree(list(self.get_queryset()), 'horizon_parent_id')}

