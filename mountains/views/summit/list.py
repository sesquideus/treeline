from cairn.views import OrderableListView

from mountains.forms.filter import FilterForm
from mountains.models import Summit


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
        'horizon': ('angle', {'nulls': 'first'}),
        'horizon-parent': 'horizon_parent__point__name',
    }

    def parse_get_arguments(self):
        super().parse_get_arguments()
        self.filter_form = FilterForm(self.request.GET or None)
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

        self.queryset = qs
        return super().get_queryset()

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            'filter_form': self.filter_form,
        }
