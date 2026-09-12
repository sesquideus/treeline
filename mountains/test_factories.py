"""Small fixture builders shared by the test modules in this app."""

from django.contrib.gis.geos import Point

from mountains.models import Col, Range, RangeSystem, Summit, SummitRange
from mountains.models.point import NamedPoint


def make_point(name, altitude, lat, lon):
    """A named point at (lat, lon) — note that `Point` itself takes (x=lon, y=lat)."""
    return NamedPoint.objects.create(
        name=name,
        altitude=altitude,
        location=Point(lon, lat, srid=4326),
    )


def make_summit(name, altitude, lat, lon, **kwargs):
    return Summit.objects.create(point=make_point(name, altitude, lat, lon), **kwargs)


def make_col(name, altitude, lat, lon, **kwargs):
    return Col.objects.create(point=make_point(name, altitude, lat, lon), **kwargs)


def make_range_system(code, name=None, **kwargs):
    return RangeSystem.objects.create(code=code, name=name or code, **kwargs)


def make_range(system, name, parent=None, **kwargs):
    return Range.objects.create(system=system, name=name, parent=parent, **kwargs)


def assign_range(summit, range):
    """A summit's membership of one range — `system` is derived, never passed."""
    return SummitRange.objects.create(summit=summit, range=range)
