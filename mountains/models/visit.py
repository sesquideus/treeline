from cairn.models import AdminModel
from django.db import models


class Visit(AdminModel):
    class Meta:
        ordering = ['-date']

    date = models.DateField()

    col = models.ForeignKey('Col', on_delete=models.CASCADE)