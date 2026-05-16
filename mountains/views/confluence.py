from django.db.models import Prefetch
from django.template.defaulttags import querystring
from django.views.generic.list import ListView as DjangoListView
from django.views.generic.detail import DetailView as DjangoDetailView

from mountains.models import Confluence, Col


class ListView(DjangoListView):
    model = Confluence
    context_object_name = 'confluences'
    template_name = 'mountains/confluence/list.html'

    def get_queryset(self):
        return super().get_queryset()


class DetailView(DjangoDetailView):
    model = Confluence
    context_object_name = 'confluence'
    template_name = 'mountains/confluence/detail.html'

    def get_queryset(self):
        return super().get_queryset().prefetch_related(
            Prefetch('cols',
                     queryset=Col.objects.with_full_name())
        )