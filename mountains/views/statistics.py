from django.views.generic import ListView

from mountains.models import Summit, Col


class StatisticsView(ListView):
    model = Summit
    template_name = 'mountains/statistics/statistics.html'

    def get_queryset(self):
        return super().get_queryset()

    def get_context_data(self, *, object_list = ..., **kwargs):
        return {
            'summits': Summit.objects.count(),
            'summitsc': Summit.objects.with_complete().count(),
            'ultras': Summit.objects.with_ultras().filter(ultra=True).count(),
            'cols': Col.objects.count(),

            'most_prominent': Summit.objects.with_prominence().with_ultras() \
                .filter(prominence_parent__isnull=False, key_col__isnull=False).order_by('-prominence')[:40],
        }
