import math

from django.utils.safestring import mark_safe
from django.contrib.gis.db import models

from core.functions.world import distance
from core.models import AdminModel, Language, Country
from core.templatetags.countries import flag


class NamedPoint(AdminModel):
    """
    A named point somewhere on the surface of the Earth. Base for all more advanced objects.
    """
    name = models.CharField(max_length=64, unique=True)

    location = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    altitude = models.FloatField()

    countries = models.ManyToManyField(Country)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.name} ({self.altitude} m)"

    def flags(self):
        return mark_safe(' '.join([flag(country.code) for country in self.countries.all()]))

    def distance_to(self, point):
        return distance(
            (self.latitude, self.longitude),
            (point.latitude, point.longitude),
        ).km

    def slope_to(self, other):
        dist = distance(
            (self.latitude, self.longitude),
            (other.latitude, other.longitude),
        ).m
        dh = other.altitude - self.altitude
        return dh / dist

    def angle_to(self, other, refraction=0.14):
        r = 6371000 * (1 + refraction)
        dist = distance(
            (self.latitude, self.longitude),
            (other.latitude, other.longitude),
        ).m
        beta = dist / r
        return math.atan(((r + other.altitude) * math.cos(beta) - (r + self.altitude)) / ((r + other.altitude) * math.sin(beta)))


class PointName(models.Model):
    """
    M2M for a point name (point, language)
    """
    point = models.ForeignKey('NamedPoint', on_delete=models.CASCADE,
                              related_name='names')

    name = models.CharField(max_length=64)
    language = models.ForeignKey(Language, on_delete=models.PROTECT)
    local = models.BooleanField(default=False)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.point} in {self.language}: {self.name}"
