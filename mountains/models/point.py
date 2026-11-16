import logging
import math

from django.db.models import Prefetch
from django.utils.safestring import mark_safe
from django.contrib.gis.db import models
from django.urls import reverse

from cairn.models import AdminModel

from core.functions.heights import HeightConversionError, to_egm2008
from core.functions.world import distance
from core.models import Language, Country, VerticalDatum
from core.templatetags.countries import flag

log = logging.getLogger(__name__)

# Which datum a point's altitude is in when nobody said: the first rule that matches wins.
# Slovak and Czech maps are in Bpv, and points on the Polish border came from them too;
# Polish-only points are taken from the current geoportal.gov.pl, which is PL-EVRF2007-NH.
# Nowhere else is assumed — an unknown datum stays unknown (null) until someone sets it.
DEFAULT_DATUMS = (
    ({'sk', 'cz'}, 'Bpv'),
    ({'pl'}, 'PL-EVRF2007'),
)

# Fields whose change makes `altitude_egm2008` stale.
HEIGHT_FIELDS = {'altitude', 'location', 'altitude_datum'}


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
    class Meta:
        ordering = ('-altitude',)

    name = models.CharField(max_length=64, null=True, blank=True, unique=True)

    location = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    altitude = models.FloatField(null=False, blank=False,
                                 help_text='As the source gives it, in `altitude_datum`')
    altitude_datum = models.ForeignKey(VerticalDatum, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name='+',
                                       help_text='What `altitude` is measured from; '
                                                 'empty means unknown')
    altitude_egm2008 = models.FloatField(null=True, blank=True, editable=False,
                                         help_text='`altitude` converted to EGM2008; '
                                                   'computed on save')

    countries = models.ManyToManyField(Country)
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    objects = NamedPointQuerySet.as_manager()

    def __str__(self):
        if self.name is not None:
            return f"{self.name} ({self.altitude}\u00A0m)"
        return f"(unnamed {self.location.y:6f} {self.location.x:6f}° {self.altitude:.1f}\u00A0m)"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or HEIGHT_FIELDS & set(update_fields):
            self.altitude_egm2008 = self.compute_altitude_egm2008()
            if update_fields is not None:
                kwargs['update_fields'] = {*update_fields, 'altitude_egm2008'}
        super().save(*args, **kwargs)

    def compute_altitude_egm2008(self):
        """
        The altitude as an EGM2008 height, or None when that cannot be known: no datum, no
        location, or a conversion PROJ cannot do here (a missing grid, a point outside it).
        Never the raw altitude passed off as converted.
        """
        if self.altitude_datum_id is None or self.location is None or self.altitude is None:
            return None
        try:
            return to_egm2008(self.location.x, self.location.y, self.altitude,
                              self.altitude_datum.crs)
        except HeightConversionError as error:
            log.warning('No EGM2008 altitude for point %s: %s', self.pk, error)
            return None

    def default_datum(self):
        """
        The datum this point's altitude is presumably in, from its countries (see
        `DEFAULT_DATUMS`); a countryless key col follows its summit. None when there is no
        good guess. Needs the point saved, for the countries.
        """
        codes = set(self.countries.values_list('code', flat=True))
        for countries, datum in DEFAULT_DATUMS:
            if codes & countries:
                return VerticalDatum.objects.get(code=datum)
        if not codes:
            summit = getattr(getattr(self, 'col', None), 'key_for', None)
            if summit is not None and summit.point_id:
                return summit.point.altitude_datum
        return None

    def get_absolute_url(self):
        """
        Always the point's own page, even when the point is a summit or a col.

        The alternative — returning the summit's URL when there is one — would make a
        point's identity depend on what happens to reference it, and would leave the
        multilingual names and notes that hang off the *point* unreachable. The page links
        onward instead.
        """
        return reverse('point-detail', kwargs={'pk': self.pk})

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

    def current_names(self):
        """
        The names the point goes by now: the local ones first, then the rest, each group by
        language priority ascending (lower is more important; see `PointName.Meta.ordering`
        — the sort is stable, so that order holds within each group). The one ordering for
        the popup and the detail pages alike.

        Filtered and sorted in Python rather than in SQL, so that a prefetched `names` —
        `with_names()`, or `point__names__language` — is all it reads.
        """
        return sorted((name for name in self.names.all() if not name.historical),
                      key=lambda name: not name.local)

    def local_names(self):
        """
        The best current name in each local language — the popup titles print them beside
        the main name. Reads the same prefetched `names` as `current_names()`; grouped by
        `language_id`, so the languages themselves need not be loaded.
        """
        seen, names = set(), []
        for name in self.current_names():
            if name.local and name.language_id not in seen:
                seen.add(name.language_id)
                names.append(name.name)
        return names

    def historical_names(self):
        """ The names it used to go by, ordered the same way. """
        return sorted((name for name in self.names.all() if name.historical),
                      key=lambda name: not name.local)

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
    class Meta:
        # Language priority counts up from the most important: English 0, Slovak 1, ...
        ordering = ('language__priority', 'language__english_name', 'name')

    point = models.ForeignKey('NamedPoint', on_delete=models.CASCADE,
                              related_name='names')

    name = models.CharField(max_length=64)
    language = models.ForeignKey(Language, on_delete=models.PROTECT)
    local = models.BooleanField(default=False)
    historical = models.BooleanField(default=False,
                                     help_text='No longer in use: a former or obsolete name')
    source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.point} in {self.language}: {self.name}"

    def to_dict(self):
        """ For a map popup: the name and the flag its language is shown with. """
        return {'name': self.name, 'flag': self.language.country_code}
