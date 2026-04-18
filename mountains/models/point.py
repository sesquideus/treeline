from django.db import models
from django.utils.safestring import mark_safe

from core.models import AdminModel, Language, Country
from core.templatetags.countries import flag


class NamedPoint(AdminModel):
    name = models.CharField(max_length=64, unique=True)

    latitude = models.FloatField()
    longitude = models.FloatField()
    altitude = models.FloatField()

    country = models.ManyToManyField(Country)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,)

    def __str__(self):
        return f"{self.name} ({self.altitude} m)"

    def flags(self):
        return mark_safe(' '.join([flag(country.code) for country in self.country.all()]))


class PointName(models.Model):
    """
    M2M for a point name (point, language)
    """
    point = models.ForeignKey('NamedPoint', on_delete=models.CASCADE,
                              related_name='names')

    name = models.CharField(max_length=64)
    language = models.ForeignKey(Language, on_delete=models.PROTECT)
    local = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.point} in {self.language}: {self.name}"
