import math

from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, F, Value, Q, CharField
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse

from mountains.models.base import GeoModel
from mountains.models.col import Col


class RiverQuerySet(models.QuerySet):
    def with_source(self):
        return self.select_related('source').prefetch_related('source__names')

    def with_parent(self):
        return self.select_related('parent').prefetch_related('parent__source__names')

    def with_siblings(self):
        return self.prefetch_related('key_for__prominence_children__key_col')


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
                queryset=River.objects.select_related('source'),
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
            complete=Q(source__location__isnull=False) & Q(source__altitude__isnull=False) & \
                     Q(mouth__isnull=False) & Q(mouth_altitude__isnull=False) & \
                     Q(parent__isnull=False) & \
                     (Q(summit__isnull=False) | Q(branches_off__isnull=False)),
        )


class River(GeoModel):
    MOUTH_CHOICES = (
        ('L', 'left'),
        ('R', 'right'),
        ('O', 'other'),
    )

    source = models.OneToOneField('NamedPoint', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    summit = models.ForeignKey('Summit', on_delete=models.SET_NULL, null=True, blank=True, related_name='rivers',
                               help_text='Nearest up-slope summit to the source')

    branches_off = models.ForeignKey('River', on_delete=models.SET_NULL, null=True, blank=True,
                                     default=None,
                                     related_name='branches',
                                     help_text='Set if the source is a branch off another river')

    mouth = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    mouth_altitude = models.FloatField(null=True, blank=True)
    mouth_side = models.CharField(max_length=1, choices=MOUTH_CHOICES, null=True, blank=True)

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
        if self.source.name:
            return f"{self.source.name}"
        else:
            return f"unnamed river ({self.source.location.y:.6f}° {self.source.location.x:.6f}°)"

    def name(self):
        return f"{self.source.name}"

    def confluence_name(self):
        return f"{self.source.name} → {self.parent.source.name}"

    def get_absolute_url(self):
        return reverse('river-detail', kwargs={'pk': self.pk})

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
