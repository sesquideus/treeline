from django.db import models
from django.urls import reverse

from core.models import AdminModel


class ConfluenceQuerySet(models.QuerySet):
    def with_siblings(self):
        return self.prefetch_related('key_for__prominence_children__key_col')


class Confluence(AdminModel):
    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False)

    def __str__(self):
        return f"{self.point}"

    def get_absolute_url(self):
        return reverse('confluence', kwargs={'pk': self.pk})
