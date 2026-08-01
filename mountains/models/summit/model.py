from typing import Self

from django.contrib.gis.db.models.functions import Distance
from django.core.exceptions import ValidationError
from django.contrib.gis.db import models
from django.db.models import Q, F, Value, CharField, Prefetch, ExpressionWrapper, FloatField, Case, When
from django.db.models.functions import Concat, Coalesce, ATan, Cos, Sin
from django.urls import reverse
from geographiclib.geodesic import Geodesic

from core.functions.world import distance
from mountains.models.base import GeoModel
from mountains.models.col import Col


class SummitQuerySet(models.QuerySet):
    def with_point(self):
        return self.select_related('point')

    def with_prominence(self):
        return (self
            .select_related('point')
            .prefetch_related(
                Prefetch(
                    'prominence_parent',
                    queryset=Summit.objects.with_point().annotate(
                        prominence=Case(
                            When(island_high_point=True, then=F('point__altitude')),
                            default=ExpressionWrapper(
                                F('point__altitude') - F('key_col__point__altitude'),
                                output_field=FloatField()
                            )
                        )
                    )
                )
            )
            .prefetch_related(
                Prefetch(
                    'key_col',
                    queryset=Col.objects.select_related('point', 'key_for__point')
                )
            )
            .annotate(
                prominence=Case(
                    When(island_high_point=True, then=F('point__altitude')),
                    default=ExpressionWrapper(
                        F('point__altitude') - F('key_col__point__altitude'),
                        output_field=FloatField()
                    )
                ),
                dominance=F('prominence') / F('point__altitude'),
                distance_to_parent=Distance('point__location', 'prominence_parent__point__location'),
        )
        )

    def with_distance_to_key_col(self):
        return self.annotate(
            distance_to_key_col=Distance('point__location', 'key_col__point__location')
        )

    def with_isolation(self):
        return self.select_related('point', 'isolation_parent__point').annotate(
            isolation=Distance('point__location', 'nearest_higher_point')
        )

    def with_countries(self):
        return self.prefetch_related('point__countries')

    def with_slope_parent(self):
        return self.select_related('slope_parent__point').annotate(
            dh=F('slope_parent__point__altitude') - F('point__altitude'),
            dd=Distance('slope_parent__point__location', 'point__location'),
            slope=F('dh') / F('dd'),
        )

    def with_horizon_parent(self):
        r = 6371000.0
        return self.select_related('horizon_parent__point').annotate(
            beta=Distance('horizon_parent__point__location', 'point__location') / Value(r),
            angle=ATan(
                ((Value(r) + F('horizon_parent__point__altitude')) * Cos(F('beta')) - (Value(r) + F('point__altitude'))) /
                ((Value(r) + F('horizon_parent__point__altitude')) * Sin(F('beta')))
            )
        )

    def with_confluence(self):
        return self.prefetch_related('key_col__confluence__point')

    def with_ultras(self):
        return self.with_prominence().annotate(
            ultra=Q(prominence__gte=1500),
        )

    def with_full_name(self):
        return self.annotate(
            full_name=Coalesce(
                F('point__name'),
                Concat(Value('unnamed ('), F('point__location'), Value(')'), output_field=CharField()),
            )
        )

    def with_complete(self):
        return self.with_prominence().annotate(
            has_point=Q(point__isnull=False),
            # An island high point needs no key col: the sea is its col, so its
            # prominence is its altitude. Every other summit must have one.
            has_key_col=(
                (Q(key_col__isnull=False) & Q(key_col__point__altitude__isnull=False))
                | Q(island_high_point=True)
            ),
            has_prominence_parent=Q(prominence_parent__point__isnull=False),
            has_isolation=Q(isolation_parent__point__isnull=False) & Q(nearest_higher_point__isnull=False),
        ).annotate(
            complete=Q(has_point=True) & Q(has_key_col=True) & Q(has_prominence_parent=True) & Q(has_isolation=True)
        )

    def only_complete(self):
        return self.with_complete().filter(
            complete=True,
        )

class Summit(GeoModel):
    class Meta:
        ordering = ('-point__altitude',)

        constraints = [
            #models.CheckConstraint(
            #    name='isolation_source_requires_parent',
            #    condition=(
            #        Q(isolation_parent__isnull=True, isolation_source__isnull=True) |
            #        Q(isolation_parent__isnull=False, isolation_source__isnull=False)
            #    )
            #)
        ]

    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False)

    prominence_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.PROTECT,
                                          related_name='prominence_children')
    key_col = models.OneToOneField('Col', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='key_for')
    prominence_source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name='prominence_data')
    island_high_point = models.BooleanField(default=False)

    isolation_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name='isolation_children',
                                         help_text='The nearest significant summit higher than this.')
    isolation_name = models.CharField(null=True, blank=True, max_length=64)
    isolation_source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name='isolation_data')
    nearest_higher_point = models.PointField(geography=True, dim=2, srid=4326, null=True, blank=True)
    # Obviously no altitude: this is equal to the altitude of this summit

    slope_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='slope_children',
                                     help_text='The summit with highest ratio of elevation change over distance')
    horizon_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name='horizon_children',
                                       help_text='The summit that is the highest point above the local horizon')
    horizon_parent_std = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name='horizon_children_std',
                                           help_text='The summit that is the highest point above the local horizon '
                                                     'with standard coefficient of refraction (0.14)')

    objects = SummitQuerySet.as_manager()

    def _check_key_col_altitude(self):
        if not (self.key_col and self.key_col.point and self.point):
            return
        if self.key_col.point.altitude >= self.point.altitude:
            raise ValidationError({
                'key_col': (
                    f'{self.key_col.point.name} '
                    f'({self.key_col.point.altitude} m) '
                    f'must be lower than {self.point.name} '
                    f'({self.point.altitude} m).'
                )
            })

    def _check_prominence_parent_altitude(self):
        if not (self.prominence_parent and self.prominence_parent.point and self.point):
            return

        if self.prominence_parent.point.altitude <= self.point.altitude:
            raise ValidationError({
                'prominence_parent': (
                    f'{self.prominence_parent.point.name} '
                    f'({self.prominence_parent.point.altitude} m) '
                    f'must be higher than {self.point.name} '
                    f'({self.point.altitude} m).'
                )
            })

        my_prominence = self.compute_prominence()
        parent_prominence = self.prominence_parent.compute_prominence()
        if my_prominence is not None and parent_prominence is not None:
            if parent_prominence <= my_prominence:
                raise ValidationError({
                    'prominence_parent': (
                        f'{self.prominence_parent.point.name} has prominence '
                        f'{parent_prominence:.0f} m, which must exceed '
                        f'the prominence of {self.point.name} '
                        f'({my_prominence:.0f} m).'
                    )
                })
        # if either prominence is unknown, skip for now

    def _check_isolation_parent_altitude(self):
        if not (self.isolation_parent and self.isolation_parent.point and self.point):
            return
        if self.isolation_parent.point.altitude <= self.point.altitude:
            raise ValidationError({
                'isolation_parent': (
                    f'{self.isolation_parent.point.name} '
                    f'({self.isolation_parent.point.altitude} m) '
                    f'must be higher than {self.point.name} '
                    f'({self.point.altitude} m).'
                )
            })

    def _check_prominence_cycle(self):
        if not self.prominence_parent:
            return
        visited = set()
        current = self.prominence_parent
        while current is not None:
            if current.pk == self.pk:
                raise ValidationError({
                    'prominence_parent': 'This would create a cycle in the prominence hierarchy.'
                })
            if current.pk in visited:
                break
            visited.add(current.pk)
            current = current.prominence_parent

    def _check_slope_cycle(self):
        if not self.slope_parent:
            return
        visited = set()
        current = self.slope_parent
        while current is not None:
            if current.pk == self.pk:
                raise ValidationError({
                    'slope_parent': 'This would create a cycle in the slope hierarchy.'
                })
            if current.pk in visited:
                break
            visited.add(current.pk)
            current = current.slope_parent


    def clean(self):
        super().clean()
        self._check_key_col_altitude()
        self._check_prominence_parent_altitude()
        self._check_isolation_parent_altitude()
        self._check_prominence_cycle()

        #if self.isolation_parent_id is None and self.isolation_source_id is not None:
        #    raise ValidationError({
        #        'isolation_source': 'Source must be null when isolation parent is null.'
        #    })

    def compute_encirclement_parent(self):
        """
        Walk up the prominence parent chain and return the first peak
        whose key col is lower than this peak's key col.
        That peak's territory encloses this one.
        # ToDo: Done by Claude, not verified yet.
        """
        if not (self.key_col and self.key_col.point):
            return None

        my_col_altitude = self.key_col.point.altitude
        visited = set()
        current = self.prominence_parent

        while current is not None:
            if current.pk in visited:
                break
            visited.add(current.pk)

            if current.key_col and current.key_col.point:
                if current.key_col.point.altitude < my_col_altitude:
                    return current

            current = current.prominence_parent

        return None

    def compute_prominence(self):
        if self.island_high_point:
            return self.point.altitude
        # Prefer the queryset annotation when present, to avoid recomputation.
        annotated = getattr(self, 'prominence', None)
        if annotated is not None:
            return annotated
        if self.key_col:
            return self.point.altitude - self.key_col.point.altitude
        else:
            return None

    def compute_isolation(self):
        if self.point and self.nearest_higher_point:
            return distance(
                (self.point.location.y, self.point.location.x),
                (self.nearest_higher_point.y, self.nearest_higher_point.x),
            )
        return None

    def isolation_vector(self):
        """ Vector of isolation, peak to nearest highest point """
        if self.point and self.nearest_higher_point:
            inv = Geodesic.WGS84.Inverse(
                self.point.location.y, self.point.location.x,
                self.nearest_higher_point.y, self.nearest_higher_point.x
            )
            return {
                'az': inv['azi1'] % 360,
                'dist': inv['s12'],
            }
        return None

    def isolation_vector_p2p(self):
        """ Vector of isolation, peak to peak """
        if self.point and self.isolation_parent:
            inv = Geodesic.WGS84.Inverse(
                self.point.location.y, self.point.location.x,
                self.isolation_parent.point.location.y, self.isolation_parent.point.location.x,
            )
            return {
                'az': inv['azi1'] % 360,
                'dist': inv['s12'],
            }
        return None

    def isolation_offset(self):
        """ Vector from nearest highest point to the associated peak """
        # FixMe: Deprecate in favour of vector version
        if self.nearest_higher_point.y and self.nearest_higher_point.x and self.isolation_parent:
            return distance(
                (self.isolation_parent.point.location.y, self.isolation_parent.point.location.x),
                (self.nearest_higher_point.y, self.nearest_higher_point.x)
            )
        return None

    def isolation_offset_vector(self):
        """ Vector from nearest highest point to the associated peak """
        if self.nearest_higher_point and self.isolation_parent:
            inv = Geodesic.WGS84.Inverse(
                self.isolation_parent.point.location.y, self.isolation_parent.point.location.x,
                self.nearest_higher_point.y, self.nearest_higher_point.x
            )
            return {
                'az': inv['azi1'] % 360,
                'dist': inv['s12'],
            }
        return None

    def distance_to_key_col(self):
        if self.key_col and self.key_col.point:
            self.point.distance_to(self.key_col.point)
        return None

    def slope_to_parent(self):
        if self.slope_parent and self.slope_parent.point:
            return self.point.slope_to(self.slope_parent.point)
        return None

    def distance_to_slope_parent(self):
        if self.slope_parent and self.slope_parent.point:
            return self.point.distance_to(self.slope_parent.point)
        return None

    def ascent_to_slope_parent(self):
        if self.slope_parent and self.slope_parent.point:
            return self.slope_parent.point.altitude - self.point.altitude
        return None

    def distance_to_horizon_parent(self):
        if self.horizon_parent and self.horizon_parent.point:
            return self.point.distance_to(self.horizon_parent.point)
        return None

    def distance_to_horizon_parent_std(self):
        if self.horizon_parent_std and self.horizon_parent_std.point:
            return self.point.distance_to(self.horizon_parent_std.point)
        return None

    def angle_to_horizon_parent(self):
        if self.horizon_parent and self.horizon_parent.point:
            return self.point.angle_to(self.horizon_parent.point)
        return None

    def angle_to_horizon_parent_std(self):
        if self.horizon_parent_std and self.horizon_parent_std.point:
            return self.point.angle_to(self.horizon_parent_std.point, refraction=0.14)
        return None

    def to_dict(self):
        prominence = self.compute_prominence()
        isolation = self.compute_isolation()
        return {
            'pk': self.pk,
            'name': self.point.name if self.point else None,
            'alt': self.point.altitude if self.point else None,
            'lat': self.point.location.y if self.point and self.point.location else None,
            'lon': self.point.location.x if self.point and self.point.location else None,
            'prom': prominence,
            'prominence_parent': self.prominence_parent_id,
            'isolation_parent': self.isolation_parent_id,
            'slope_parent': self.slope_parent_id,
            'horizon_parent': self.horizon_parent_id,
            'ilp': {
                'name': self.isolation_name,
                'dist': isolation.m if isolation is not None else None,
                'lat': self.nearest_higher_point.y if self.nearest_higher_point else None,
                'lon': self.nearest_higher_point.x if self.nearest_higher_point else None,
            },
            'kc': self.key_col_id,
        }

    def to_geojson(self):
        if not self.point or not self.point.location:
            return None
        return {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [self.point.location.x, self.point.location.y],
            },
            'properties': {
                'type': 'summit',
                **self.to_dict(),
            }
        }

    def prominence_ancestors(self) -> list[Self]:
        ancestors = []
        visited = set()
        current = self
        while current.prominence_parent_id:
            if current.prominence_parent_id in visited:
                break
            visited.add(current.prominence_parent_id)
            current = Summit.objects.select_related(
                'point',
                'key_col__point',
            ).get(pk=current.prominence_parent_id)
            ancestors.append(current.to_dict())
        return ancestors

    def isolation_ancestors(self) -> list[Self]:
        ancestors = []
        visited = set()
        current = self
        while current.isolation_parent_id:
            if current.isolation_parent_id in visited:
                break
            visited.add(current.isolation_parent_id)
            current = Summit.objects.select_related(
                'point',
            ).get(pk=current.isolation_parent_id)
            ancestors.append(current.to_dict())
        return ancestors

    def prominence_children_list(self):
        return [
            c.to_dict()
            for c in Summit.objects.select_related(
                'point',
                'key_col__point',
            ).filter(prominence_parent=self)
        ]

    def isolation_children_list(self):
        return [
            c.to_dict()
            for c in Summit.objects.select_related(
                'point',
            ).filter(isolation_parent=self)
        ]

    def is_complete(self):
        return self.key_col is not None and self.point is not None and self.isolation_parent is not None and \
            self.nearest_higher_point is not None

    def get_absolute_url(self):
        return reverse('summit-detail', kwargs={'pk': self.pk})

    def __str__(self):
        if self.point.name is not None:
            return f"{self.point.__str__()}"
        return "(unnamed)"

    def name(self):
        if self.point.name:
            return f"{self.point.name}"
        else:
            return f"unnamed ({self.point.location.y:.3f}° {self.point.location.x:.3f}° {self.point.altitude:.0f} m)"
