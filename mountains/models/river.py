import math

from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, F, Value, Q, CharField
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse
from django.utils.html import format_html

from mountains.models.base import GeoModel
from mountains.models.col import Col


def summit_dict(summit):
    """
    A summit as the river popup wants it, or None.

    `Summit.point` is nullable, so a summit without one yields None rather than a label
    with a hole in it. `pk` is a **Summit** pk — `static/js/map.js` builds `/summit/<pk>/`
    from it and looks it up in the summit skeleton, and both go quietly wrong if anything
    else is put here.
    """
    if summit is None or summit.point is None:
        return None
    return {'pk': summit.pk, 'name': summit.point.display_name(),
            'alt': summit.point.altitude}


def point_dict(point):
    """
    A named point as the river popup wants it, or None.

    `pk` is a **NamedPoint** pk, and `summit` the pk of the Summit that point belongs to if
    it is one at all. They are kept apart on purpose: the popup links through the point, but
    the hover highlight can only find a marker in the summit skeleton, which carries summit
    pks and nothing else. Collapsing the two would build `/summit/<namedpoint pk>/` — a URL
    that resolves to an unrelated mountain rather than 404ing.

    `getattr` rather than a try/except: Django's `RelatedObjectDoesNotExist` subclasses
    `AttributeError` precisely so a missing reverse one-to-one reads as absent.
    """
    if point is None:
        return None
    summit = getattr(point, 'summit', None)
    return {'pk': point.pk, 'summit': summit.pk if summit else None,
            'name': point.display_name(), 'alt': point.altitude}


class RiverQuerySet(models.QuerySet):
    def with_source(self):
        return self.select_related('source').prefetch_related('source__names')

    def with_parent(self):
        return self.select_related('parent').prefetch_related('parent__source__names')

    def with_source_summit(self):
        """The dominant peak above the source — a catalogued summit, and its point."""
        return self.select_related('source_summit__point')

    def with_watershed_high_point(self):
        """
        The highest ground in the basin, which is a `NamedPoint` and need not be a summit at
        all — that is the whole reason it is not a `Summit` FK.

        The `__summit` hop is a *reverse* one-to-one, and it is what lets `point_dict()` say
        whether the point is a summit without a query per river.
        """
        return self.select_related('watershed_high_point__summit')

    def with_full_name(self):
        return self.annotate(
            full_name=Concat(
                F('source__name'),
                Value(' ('),
                F('source__altitude'),
                Value(')'),
                output_field=CharField(),
            )
        )

    def with_displacement(self):
        return self.annotate(
            displacement=Distance('source__location', 'mouth'),
        )

    def with_tributaries(self):
        return self.prefetch_related(
            Prefetch(
                'tributaries',
                queryset=River.objects.with_source().with_full_name().order_by('-mouth_altitude'),
            )
        )

    def with_branches(self):
        """Rivers that bifurcate off this one; their sources are junctions on it."""
        return self.prefetch_related(
            Prefetch(
                'branches',
                queryset=River.objects.with_source().with_full_name().order_by('-source__altitude'),
            )
        )

    def with_cols(self):
        return self.prefetch_related(
            Prefetch(
                'cols',
                queryset=Col.objects.with_minor().order_by('-depth'),
            )
        )

    def with_direct_length(self):
        return self.annotate(
            direct_length=Distance('source__location', 'mouth'),
        )

    def with_db_status(self):
        return self.annotate(
            complete=(
                Q(source__location__isnull=False) & Q(source__altitude__isnull=False) &
                Q(mouth__isnull=False) & Q(mouth_altitude__isnull=False) & Q(mouth_side__isnull=False) &
                Q(parent__isnull=False) & Q(watershed_high_point__isnull=False) &
                (Q(source_summit__isnull=False) | Q(branches_off__isnull=False))
            ),
        )


class River(GeoModel):
    MOUTH_CHOICES = (
        ('L', 'left'),      # Left tributary
        ('R', 'right'),     # Right tributary
        ('O', 'other'),     # Not decidable
        ('S', 'sea'),       # Reaches the sea, so no bank to name
    )

    source = models.OneToOneField('NamedPoint', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    source_summit = models.ForeignKey('Summit', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='rivers',
                                      help_text='Dominant up-slope summit from the source')
    # A NamedPoint and not a Summit: the highest ground in a basin is often a named point
    # nobody has catalogued as a summit. When it is one, `point.summit` gives it back.
    watershed_high_point = models.ForeignKey('NamedPoint', on_delete=models.SET_NULL,
                                             null=True, blank=True, related_name='drains',
                                             help_text='Highest point within the watershed')

    branches_off = models.ForeignKey('River', on_delete=models.SET_NULL, null=True, blank=True,
                                     default=None,
                                     related_name='branches',
                                     help_text='Set if the source is a branch off another river')

    mouth = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    mouth_altitude = models.FloatField(null=True, blank=True)
    mouth_side = models.CharField(max_length=1, choices=MOUTH_CHOICES, null=True, blank=True)

    #: The mark shown for each `mouth_side`: a sprite id in
    #: `mountains/blocks/mouth-side-sprite.html`, and the wording behind it.
    #:
    #: Not an arrow. Unicode has no glyph for "a confluence with the minor stream on the
    #: left", and every arrow that comes close has to be read through a convention — which
    #: is precisely the thing that is ambiguous here, since left and right bank are named
    #: looking downstream and that is not the side they land on in a drawing. The sprite
    #: sidesteps the argument by drawing the confluence instead of encoding a direction:
    #: thick line the main river, thin line the tributary joining it, and the side the thin
    #: line is on *is* the answer.
    #:
    #: `None` for `mouth_side` is not the same as 'O' — 'O' records that the side was looked
    #: at and could not be decided, `None` that nobody has looked.
    MOUTH_SIDE_MARKS = {
        'L': ('left', 'joins from the left bank, looking downstream'),
        'R': ('right', 'joins from the right bank, looking downstream'),
        'O': ('undecided', 'joins, but the bank could not be decided'),
        'S': ('sea', 'reaches the sea, so neither bank applies'),
    }

    parent = models.ForeignKey('River', on_delete=models.CASCADE, null=True, blank=True, related_name='tributaries')

    objects = RiverQuerySet.as_manager()

    def _check_mouth_altitude(self):
        if not (self.parent and self.mouth_altitude is not None and self.parent.mouth_altitude is not None):
            return
        if self.mouth_altitude < self.parent.mouth_altitude:
            raise ValidationError({
                'mouth_altitude': (
                    f'{self.name()} mouth ({self.mouth_altitude:.1f} m) '
                    f'must not be lower than the mouth of its parent '
                    f'{self.parent.name()} ({self.parent.mouth_altitude:.1f} m).'
                )
            })

    def clean(self):
        super().clean()
        self._check_mouth_altitude()

    def __str__(self):
        if self.source:
            if self.source.name:
                return f"{self.source.name}"
            else:
                return f"unnamed river ({self.source.location.y:.6f}° {self.source.location.x:.6f}°)"
        else:
            return f"unsourced river"

    def name(self):
        return f"{self.source.name}"

    def confluence_name(self):
        return f"{self.source.name} → {self.parent.source.name}"

    def get_absolute_url(self):
        return reverse('river-detail', kwargs={'pk': self.pk})

    def mouth_side_mark(self):
        """
        The sprite to draw and the wording behind it, or `None` while the side is unrecorded.

        `code` is half of a contract with `static/js/map.js`, which builds the same `<use>`
        reference for the popup: both spell the sprite id `mouth-side-{code}`, and the ids
        themselves live in `mountains/blocks/mouth-side-sprite.html`.
        """
        mark = self.MOUTH_SIDE_MARKS.get(self.mouth_side)
        return {'code': mark[0], 'title': mark[1]} if mark else None

    def mouth_side_abbr(self):
        """`mouth_side_mark()` as the markup the templates print, or nothing at all."""
        mark = self.mouth_side_mark()
        if mark is None:
            return ''
        return format_html(
            '<abbr title="{}"><svg class="mouth-side" role="img">'
            '<use href="#mouth-side-{}"></use></svg></abbr>',
            mark['title'], mark['code'])

    def to_dict(self):
        return {
            'pk': self.pk,
            'name': self.__str__(),
            'source': {
                'lat': self.source.location.y,
                'lon': self.source.location.x,
                'alt': self.source.altitude,
            },
            'parent': {
                'name': self.parent.source.name,
                'id': self.parent_id,
            } if self.parent else None,
            'mouth': {
                'lat': self.mouth.y,
                'lon': self.mouth.x,
                'alt': self.mouth_altitude,
            } if self.mouth else None,
            # Which bank of the parent this one joins; the popup prints it beside the
            # parent's name, so it travels whether or not the mouth has coordinates.
            'mouth_side': self.mouth_side_mark(),
            # The two landmarks a river names: the peak above its source, and the highest
            # point of the basin it drains. Different claims, usually different places, and
            # different kinds of object — see `summit_dict` and `point_dict`.
            'source_summit': summit_dict(self.source_summit),
            'watershed_high_point': point_dict(self.watershed_high_point),
        }

    def get_waypoints(self):
        """
        Get an ordered list of waypoints for this river.

        Between the source and the mouth the channel passes two kinds of junction: the
        mouths of its tributaries, and — where the river bifurcates — the sources of the
        rivers that branch off it. Both are ordered together by descending altitude, the
        order in which the water reaches them; taking only the tributaries would drop a
        bifurcation out of the polyline entirely.

        The related managers are read with `.all()` and filtered in Python so that a
        caller who prefetched them (`with_tributaries().with_branches()`) pays no extra
        query per river.
        """
        junctions = [
            (trib.mouth_altitude, trib.mouth)
            for trib in self.tributaries.all()
            if trib.mouth is not None
        ] + [
            (branch.source.altitude, branch.source.location)
            for branch in self.branches.all()
            if branch.source is not None and branch.source.location is not None
        ]
        # An unknown altitude sorts first, which is what NULLS FIRST gave us under the
        # previous `order_by('-mouth_altitude')`.
        junctions.sort(key=lambda junction: math.inf if junction[0] is None else junction[0],
                       reverse=True)

        points = []
        if self.source and self.source.location:
            points.append(self.source.location)
        points.extend(location for _, location in junctions)
        if self.mouth:
            points.append(self.mouth)

        return points

    def to_geojson(self):
        waypoints = self.get_waypoints()

        if len(waypoints) < 2:
            return None
        else:
            return {
                'type': 'Feature',
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[p.x, p.y] for p in waypoints],
                },
                'properties': {
                    'type': 'river',
                    **self.to_dict(),
                }
            }
