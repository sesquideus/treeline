class TreeMixin:
    def get_tree(self, roots, parent_attr, children_attr):
        """Build a nested tree from a flat queryset."""
        def build_node(summit):
            return {
                'pk': summit.pk,
                'name': summit.point.name if summit.point else None,
                'alt': summit.point.altitude if summit.point else None,
                'lat': summit.point.location.y if summit.point and summit.point.location else None,
                'lon': summit.point.location.x if summit.point and summit.point.location else None,
                'children': [
                    build_node(child)
                    for child in getattr(summit, children_attr).all()
                    if child.point
                ],
            }
        return [build_node(root) for root in roots]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context