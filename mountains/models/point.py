import math

from django.utils.safestring import mark_safe
from django.contrib.gis.db import models

from cairn.models import AdminModel

from core.functions.world import distance
from core.models import Language, Country
from core.templatetags.countries import flag


class NamedPoint(AdminModel):
    """
    A named point somewhere on the surface of the Earth. Base for all more advanced objects.
    """
    name = models.CharField(max_length=64, null=True, blank=True, unique=True)

    location = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    altitude = models.FloatField(null=False, blank=False)

    countries = models.ManyToManyField(Country)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        if self.name is not None:
            return f"{self.name}"
        return "(unnamed)"

    def full_name(self):
        if self.name is not None:
            return f"{self.name} ({self.altitude:.1f})"
        return "(unnamed)"

    def flags(self):
        return mark_safe(' '.join([flag(country.code) for country in self.countries.all()]))

    def distance_to(self, point):
        return distance(
            (self.location.y, self.location.x),
            (point.location.y, point.location.x),
        ).km

    def slope_to(self, other):
        dist = distance(
            (self.location.y, self.location.x),
            (other.location.y, other.location.x),
        ).m
        dh = other.altitude - self.altitude
        return dh / dist

    def angle_to(self, other, refraction=0.0):
        r = 6371000 / (1 - refraction)
        dist = distance(
            (self.location.y, self.location.x),
            (other.location.y, other.location.x),
        ).m
        beta = dist / r
        return math.atan(
            ((r + other.altitude) * math.cos(beta) - (r + self.altitude)) / ((r + other.altitude) * math.sin(beta))
        )


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
