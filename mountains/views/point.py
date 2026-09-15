from django.db.models import Prefetch
from django.views.generic.detail import DetailView as DjangoDetailView

from mountains.models import River
from mountains.models.point import NamedPoint, PointName


class DetailView(DjangoDetailView):
    """
    A named point's own page.

    Most points are the anchor of a summit or a col and are reached through those; this page
    exists for the ones that are neither — the 800-odd named places that are now allowed to
    be a river's watershed high point. It is a leaf: no list, no menu entry, reached from
    whatever references it.
    """
    model = NamedPoint
    context_object_name = 'point'
    template_name = 'mountains/point/detail.html'

    def get_queryset(self):
        return (super().get_queryset()
                # All three are reverse one-to-ones and all three are usually absent, which
                # is exactly when select_related earns its place: without it the template's
                # "is this a summit?" questions are one query each.
                .select_related('summit', 'col', 'confluence')
                .prefetch_related(
                    'countries',
                    'notes',
                    Prefetch('names', queryset=PointName.objects.select_related('language')),
                    Prefetch('drains', queryset=River.objects.with_source()),
                ))
