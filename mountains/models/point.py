import math

from django.db.models import Prefetch
from django.utils.safestring import mark_safe
from django.contrib.gis.db import models

from cairn.models import AdminModel

from core.functions.world import distance
from core.models import Language, Country
from core.templatetags.countries import flag


class NamedPointQuerySet(models.QuerySet):
    def with_names(self):
        """
        The multilingual `PointName` rows with their languages. The lookup is `names` — the
        related name on `PointName.point`; `name` is this model's own CharField, and
        prefetching it raises.
        """
        return self.prefetch_related(
            Prefetch('names',
                     queryset=PointName.objects.select_related('language'))
        )


class NamedPoint(AdminModel):
    """
    A named point somewhere on the surface of the Earth. Base for all more advanced objects.
    """
    name = models.CharField(max_length=64, null=True, blank=True, unique=True)

    location = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    altitude = models.FloatField(null=False, blank=False)

    countries = models.ManyToManyField(Country)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    objects = NamedPointQuerySet.as_manager()

    def __str__(self):
        if self.name is not None:
            return f"{self.name} ({self.altitude}\u00A0m)"
        return f"(unnamed {self.location.y:6f} {self.location.x:6f}° {self.altitude:.1f}\u00A0m)"

    def full_name(self):
        if self.name is not None:
            return f"{self.name} ({self.altitude:.1f}\u00A0m)"
        return "(unnamed)"

    def coordinates(self, digits=5):
        """ Where it is, written out: '49.16451° N 19.90312° E'. """
        if not self.location:
            return None
        latitude, longitude = self.location.y, self.location.x
        return (f"{abs(latitude):.{digits}f}° {'N' if latitude >= 0 else 'S'} "
                f"{abs(longitude):.{digits}f}° {'E' if longitude >= 0 else 'W'}")

    def display_name(self):
        """
        What to print for this point: its name, or where it is when it has none —
        '(unnamed 49.16451° N 19.90312° E)'. Names are nullable and cols in particular are
        usually unnamed, so a template that prints `point.name` prints `None`; this is what
        it should print instead.
        """
        if self.name:
            return self.name
        coordinates = self.coordinates()
        return f"(unnamed {coordinates})" if coordinates else "(unnamed)"

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
