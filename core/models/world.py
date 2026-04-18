from django.db import models

from core.models import AdminModel


class Country(AdminModel):
    class Meta:
        verbose_name_plural = 'Countries'

    code = models.SlugField(max_length=3, unique=True)
    name = models.CharField(max_length=30)
    full_name = models.CharField(max_length=255, unique=True)
    english_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Language(models.Model):
    class Meta:
        ordering = ['priority', 'english_name']

    iso639_1 = models.SlugField(max_length=2, unique=True)
    iso639_3 = models.SlugField(max_length=3, unique=True, null=True, blank=True) # FixMe for now can be empty

    name = models.CharField(max_length=64, unique=True)
    english_name = models.CharField(max_length=64, unique=True)
    priority = models.PositiveSmallIntegerField()

    def __str__(self):
        return self.english_name


