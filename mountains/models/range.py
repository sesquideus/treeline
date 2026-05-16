from django.db import models

from cairn.models import AdminModel


class Range(AdminModel):
    name = models.CharField(max_length=255)

    parent = models.ForeignKey('Range', null=True, blank=True)