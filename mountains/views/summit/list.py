from cairn.views import OrderableListView
from django.contrib.gis.measure import D

from mountains.forms.filter import SummitFilterForm
from mountains.models import Summit


def within(qs, name, bounds, convert=float):
    """Apply an inclusive `(low, high)` range to a field or annotation; either end may be None."""
    low, high = bounds
    if low is not None:
        qs = qs.filter(**{f'{name}__gte': convert(low)})
    if high is not None:
        qs = qs.filter(**{f'{name}__lte': convert(high)})
    return qs


class MountainListView(OrderableListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/summit/list/list.html'

    ORDERING = {
        'name': 'point__name',
        'altitude': 'point__altitude',
        'parent-name': 'prominence_parent__point__name',
        'parent-alt': 'prominence_parent__point__altitude',
        'parent-dist': 'distance_to_parent',
        'prominence': 'prominence',
        'dominance': 'dominance',
        'key-col': 'key_col__point__name',
        'key-col-alt': 'key_col__point__altitude',
        'key-col-dist': 'distance_to_key_col',
        'nhn': 'isolation_parent__point__name',
        'isolation': 'isolation',
        'slope': 'slope',
        'slope-parent': 'slope_parent__point__name',
        'slope-parent-dist': 'dd',
        'slope-parent-alt-diff': 'dh',
        'horizon': ('angle', {'nulls': 'first'}),
        'horizon-parent': 'horizon_parent__point__name',
        'horizon-parent-dist': 'distance_to_horizon',
    }

    def parse_get_arguments(self):
        super().parse_get_arguments()
        self.filter_form = SummitFilterForm(self.request.GET or None)
        self.filter_form.is_valid()  # all fields are optional, so this just populates cleaned_data

    def get_queryset(self):
        qs = (Summit.objects
            .with_point()
            .with_prominence()
            .with_distance_to_key_col()
            .with_isolation()
            .with_slope_parent()
            .with_horizon_parent()
            .with_countries()
            .with_full_name()
        )

        data = getattr(self.filter_form, 'cleaned_data', {})
        if countries := data.get('countries'):
            # Filtering across the countries M2M join can duplicate summit rows
            qs = qs.filter(point__countries__in=countries).distinct()
        if name := data.get('name'):
            qs = qs.filter(point__name__icontains=name)
        if altitude := data.get('altitude'):
            qs = within(qs, 'point__altitude', altitude)
        if prominence := data.get('prominence'):
            # Both metric filters drop the summits whose metric is NULL — no key col means no
            # prominence, no nearest higher point means no isolation (Everest has neither).
            qs = within(qs, 'prominence', prominence)
        if isolation := data.get('isolation'):
            # `isolation` is a Distance annotation, so its bounds have to be measures too;
            # the form takes kilometres because that is what the column shows.
            qs = within(qs, 'isolation', isolation, convert=lambda km: D(km=km))

        self.queryset = qs
        return super().get_queryset()

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            'filter_form': self.filter_form,
        }
