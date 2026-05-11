from django.views.generic import DetailView

from ..models import Col


class ColView(DetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col/detail.html'

    def get_queryset(self):
        return super().get_queryset().select_related('point')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context |= {
            'friend_cols': Col.objects.filter(confluence=self.object.confluence)
        }
        return context

