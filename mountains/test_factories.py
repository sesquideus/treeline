"""Small fixture builders shared by the test modules in this app."""

from django.contrib.gis.geos import Point

from core.models.world import Country
from mountains.models import Col, Range, RangeSystem, Source, Summit, SummitRange
from mountains.models.point import NamedPoint


def make_point(name, altitude, lat, lon):
    """A named point at (lat, lon) — note that `Point` itself takes (x=lon, y=lat)."""
    return NamedPoint.objects.create(
        name=name,
        altitude=altitude,
        location=Point(lon, lat, srid=4326),
    )


def fixture_source():
    """The source a test's parents cite when the test does not care which."""
    return Source.objects.get_or_create(name='test fixture')[0]


def make_summit(name, altitude, lat, lon, **kwargs):
    """
    A summit; a prominence or isolation parent comes with a source, because the database
    refuses one without (migration 0038). Pass `*_source` to choose it.
    """
    for kind in ('prominence', 'isolation'):
        if kwargs.get(f'{kind}_parent') is not None:
            kwargs.setdefault(f'{kind}_source', fixture_source())
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


def make_country(code, name=None):
    """A country, named three times over because the model insists on all three."""
    name = name or code
    return Country.objects.create(code=code, name=name,
                                  full_name=f'{name} (full)', english_name=name)
