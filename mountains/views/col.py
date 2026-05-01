from django.views.generic import DetailView

from ..models import Col


class ColView(DetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col.html'

    def get_queryset(self):
        return super().get_queryset().select_related('point')

