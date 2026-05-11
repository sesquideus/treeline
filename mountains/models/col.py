from django.db import models
from django.db.models import Prefetch, F
from django.urls import reverse

from core.models import AdminModel


class ColQuerySet(models.QuerySet):
    def with_siblings(self):
        return self.prefetch_related('key_for__prominence_children__key_col')

    def with_point(self):
        return self.select_related('point')


class Col(AdminModel):
    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False)
    confluence = models.ForeignKey('Confluence', on_delete=models.CASCADE, null=True, blank=True, related_name='cols')

    objects = ColQuerySet.as_manager()

    def __str__(self):
        return f"{self.point}"

    def get_absolute_url(self):
        return reverse('col', kwargs={'pk': self.pk})
