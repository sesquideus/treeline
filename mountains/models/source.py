from django.db import models

from cairn.models import AdminModel


class Source(AdminModel):
    class Meta:
        ordering = ['quality']

    name = models.CharField(max_length=120)
    quality = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return self.name
