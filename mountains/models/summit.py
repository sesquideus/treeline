from django.core.exceptions import ValidationError
from django.db.models import Q, F
from django.urls import reverse
from geographiclib.geodesic import Geodesic

from core.functions.world import distance

from django.db import models

from core.models import AdminModel


class SummitManager(models.Manager):
    def with_prominence(self):
        return self.annotate(
            prominence=F('point__altitude') - F('key_col__point__altitude'),
        )

class Summit(AdminModel):
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
    key_col = models.ForeignKey('Col', null=True, blank=True, on_delete=models.PROTECT,
                                related_name='key_for')
    encirclement_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.PROTECT,
                                            related_name='encirclement_children')
    prominence_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.PROTECT,
                                          related_name='prominence_children')
    prominence_source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name='prominence_data')
    island_high_point = models.BooleanField(default=False)

    isolation_parent = models.ForeignKey('Summit', null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name='isolation_children',
                                         help_text='The nearest significant summit higher than this.')
    isolation_name = models.CharField(null=True, blank=True, max_length=64)
    isolation_source = models.ForeignKey('Source', null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name='isolation_data')
    isolation_latitude = models.FloatField(null=True, blank=True,
                                           help_text='The exact latitude of the nearest highest point')
    isolation_longitude = models.FloatField(null=True, blank=True,
                                            help_text='The exact longitude of the nearest highest point')
    # Obviously no altitude: this is equal to the altitude of this summit

    objects = SummitManager()

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

        my_prominence = self.prominence()
        parent_prominence = self.prominence_parent.prominence()
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
        # ToDo: Done by Claude, not verified yet. But maybe we can get rid of encirclement anyway
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

    def prominence(self):
        if self.island_high_point:
            return self.point.altitude
        if self.key_col:
            return self.point.altitude - self.key_col.point.altitude
        else:
            return None

    def isolation(self):
        if self.point and self.isolation_latitude and self.isolation_longitude:
            return distance(
                (self.point.latitude, self.point.longitude),
                (self.isolation_latitude, self.isolation_longitude)
            )
        return None

    def isolation_vector(self):
        """ Vector of isolation, peak to nearest highest point """
        if self.point and self.isolation_latitude and self.isolation_longitude:
            inv = Geodesic.WGS84.Inverse(
                self.point.latitude, self.point.longitude,
                self.isolation_latitude, self.isolation_longitude
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
                self.point.latitude, self.point.longitude,
                self.isolation_parent.point.latitude, self.isolation_parent.point.longitude,
            )
            return {
                'az': inv['azi1'] % 360,
                'dist': inv['s12'],
            }
        return None

    def isolation_offset(self):
        """ Vector from nearest highest point to the associated peak """
        # FixMe: Deprecate in favour of vector version
        if self.isolation_latitude and self.isolation_longitude and self.isolation_parent:
            return distance(
                (self.isolation_parent.point.latitude, self.isolation_parent.point.longitude),
                (self.isolation_latitude, self.isolation_longitude)
            )
        return None

    def isolation_offset_vector(self):
        """ Vector from nearest highest point to the associated peak """
        if self.isolation_latitude and self.isolation_longitude and self.isolation_parent:
            inv = Geodesic.WGS84.Inverse(
                self.isolation_parent.point.latitude, self.isolation_parent.point.longitude,
                self.isolation_latitude, self.isolation_longitude
            )
            return {
                'az': inv['azi1'] % 360,
                'dist': inv['s12'],
            }
        return None

    def distance_to_key_col(self):
        if self.key_col and self.key_col.point:
            return distance(
                (self.point.latitude, self.point.longitude),
                (self.key_col.point.latitude, self.key_col.point.longitude)
            )
        return None


    def to_dict(self):
        isolation = self.isolation()
        return {
            'pk': self.pk,
            'name': self.point.name if self.point else None,
            'alt': self.point.altitude if self.point else None,
            'lat': self.point.latitude if self.point else None,
            'lon': self.point.longitude if self.point else None,
            'prom': self.prominence(),
            'ilp': {
                'name': self.isolation_name,
                'dist': isolation.m if isolation is not None else None,
                'lat': self.isolation_latitude,
                'lon': self.isolation_longitude,
            },
            'kc': {
                'name': self.key_col.point.name if self.key_col and self.key_col.point else None,
                'lat': self.key_col.point.latitude if self.key_col and self.key_col.point else None,
                'lon': self.key_col.point.longitude if self.key_col and self.key_col.point else None,
                'alt': self.key_col.point.altitude if self.key_col and self.key_col.point else None,
            } if self.key_col and self.key_col.point else None,
        }

    def prominence_ancestors(self):
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

    def isolation_ancestors(self):
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
            self.isolation_latitude is not None and self.isolation_longitude is not None

    def get_absolute_url(self):
        return reverse('summit-detail', kwargs={'pk': self.pk})

    def __str__(self):
        return f"{self.point}"