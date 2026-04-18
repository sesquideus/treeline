from django.db import models
from django.urls import reverse

from core.models import AdminModel


class Col(AdminModel):
    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False)

    def __str__(self):
        return f"{self.point}"

    def get_absolute_url(self):
        return reverse('col', kwargs={'pk': self.pk})
