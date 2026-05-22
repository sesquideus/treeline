from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.db.models import Prefetch, F, Value, Q, CharField
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse

from mountains.models.base import GeoModel


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

    def with_db_status(self):
        return self.annotate(
            complete=Q(source__location__isnull=False) & Q(source__altitude__isnull=False) & \
                     Q(mouth__isnull=False) & Q(mouth_altitude__isnull=False) & \
                     Q(parent__isnull=False) & Q(summit__isnull=False),
        )


class River(GeoModel):
    source = models.OneToOneField('NamedPoint', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    summit = models.ForeignKey('Summit', on_delete=models.SET_NULL, null=True, blank=True, related_name='rivers')

    mouth = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    mouth_altitude = models.FloatField(null=True, blank=True)

    parent = models.ForeignKey('River', on_delete=models.CASCADE, null=True, blank=True, related_name='tributaries')

    objects = RiverQuerySet.as_manager()

    def __str__(self):
        return f"{self.source}"

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
            'mouth': {
                'lat': self.mouth.y,
                'lon': self.mouth.x,
                'alt': self.mouth_altitude,
            } if self.mouth else None,
        }

    def get_waypoints(self):
        points = []
        if self.source and self.source.location:
            points.append(self.source.location)
        for trib in self.tributaries.filter(mouth__isnull=False).order_by('-mouth_altitude'):
            points.append(trib.mouth)
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