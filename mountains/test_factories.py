"""Small fixture builders shared by the test modules in this app."""

from django.contrib.gis.geos import Point

from mountains.models import Col, Summit
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
