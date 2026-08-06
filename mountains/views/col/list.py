from cairn.views import OrderableListView

from ...forms.filter import FilterForm
from ...models import Col


class ListView(OrderableListView):
    model = Col
    context_object_name = 'cols'
    template_name = 'mountains/col/list.html'

    ORDERING = {
        'name': 'point__name',
        'altitude': 'point__altitude',
        'minor-name': 'key_for__point__name',
        'minor-alt': 'key_for__point__altitude',
        'depth': 'depth',
        'major-name': 'key_for__prominence_parent__point__name',
        'major-alt': 'key_for__prominence_parent__point__altitude',
        'river-name': 'confluence_river__source__name',
        'river-alt': 'confluence_river__source__altitude',
        'confluence-alt': 'confluence_river__mouth_altitude',
    }

    def parse_get_arguments(self):
        super().parse_get_arguments()
        self.filter_form = FilterForm(self.request.GET or None)
        self.filter_form.is_valid()  # all fields are optional, so this just populates cleaned_data

    def get_queryset(self):
        qs = super().get_queryset().with_point().with_minor().with_rivers().with_countries()

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
