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

    def with_slope_to_key_col(self):
        # Signed like every other slope here — the way to the key col goes down, so the
        # gradient is negative. `key_col_dh` is the drop as a negative height difference.
        return self.annotate(
            key_col_dh=F('key_col__point__altitude') - F('point__altitude'),
            key_col_dd=Distance('point__location', 'key_col__point__location'),
            slope_to_key_col=F('key_col_dh') / F('key_col_dd'),
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
            # Not `distance_to_horizon_parent`: that name is already a model method returning km,
            # and an annotation would shadow it on every queryset that calls this.
            distance_to_horizon=Distance('horizon_parent__point__location', 'point__location'),
            beta=F('distance_to_horizon') / Value(r),
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

    def skeleton_features(self):
        """
        GeoJSON features carrying only what the global map *draws and joins with* — the rest
        of a summit's properties are read by the popup of the one feature under the cursor
        and are served separately by the viewport detail endpoint.

        Built straight from `values_list`, without instantiating models: that is the whole
        point of the split — an order of magnitude cheaper than `to_geojson()` per summit.
        The property names are the client API (`static/js/map.js`, `static/js/styles.js`
        read `prom`, `kc`, `ilp` and the `*_parent` ids by name), so they must match
        `to_dict()` exactly. `name` is here so a popup has a caption before its detail
        arrives.
        """
        rows = list(self.values_list(
            'pk', 'point__location', 'point__name', 'point__altitude',
            'key_col__point__altitude', 'island_high_point',
            'prominence_parent_id', 'isolation_parent_id', 'slope_parent_id',
            'horizon_parent_id', 'key_col_id', 'nearest_higher_point',
            'slope_computed', 'horizon_computed',
        ))
        # The highest summit in the collection. A slope parent has to be *higher* than its
        # child (see the compute action), so for that one summit a missing slope parent is
        # the truth rather than a gap in the data — and the map says so with a different
        # glyph. Every other missing one means nobody has computed it yet.
        highest = max((row[3] for row in rows if row[3] is not None), default=None)
        features = []
        for (pk, location, name, altitude, col_altitude, island,
             prominence_parent, isolation_parent, slope_parent, horizon_parent,
             key_col, nearest_higher_point, slope_computed, horizon_computed) in rows:
            if location is None:
                continue
            # Same island-high-point branch as with_prominence(): for those the sea is the
            # col, and a plain subtraction would leave them without a prominence band.
            if island:
                prominence = altitude
            elif col_altitude is not None and altitude is not None:
                prominence = altitude - col_altitude
            else:
                prominence = None
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [location.x, location.y]},
                'properties': {
                    'type': 'summit',
                    'pk': pk,
                    'name': name,
                    'prom': prominence,
                    'prominence_parent': prominence_parent,
                    'isolation_parent': isolation_parent,
                    'slope_parent': slope_parent,
                    'horizon_parent': horizon_parent,
                    'kc': key_col,
                    # Only on the summit nothing is higher than; see `highest` above.
                    **({'top': True} if altitude is not None and altitude == highest else {}),
                    # Only where a parent is missing, which is the one case the map asks:
                    # was it looked for and not found, or never looked for at all?
                    **({'slope_computed': True}
                       if slope_parent is None and slope_computed else {}),
                    **({'horizon_computed': True}
                       if horizon_parent is None and horizon_computed else {}),
                    # Only the position: the lineage layer routes through it, and the
                    # isolation point layer marks it. Its name and distance are detail.
                    'ilp': {
                        'lon': nearest_higher_point.x,
                        'lat': nearest_higher_point.y,
                    } if nearest_higher_point else None,
                },
            })
        return features

    def only_complete(self):
        return self.with_complete().filter(
            complete=True,
        )

    def by_angle(self):
        """
        Ordered by the angle above the horizon. `with_point()` because
        `with_horizon_parent()` joins the *parent's* point and not the summit's own, and a
        row about a summit asks for its name and its altitude before anything else — one
        query apiece without it.
        """
        return self.with_point().with_horizon_parent().order_by('angle')

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

    # When each of those was last worked out, stamped by the admin actions that compute them.
    # Without it a null parent is two different states at once: nothing to point at, or
    # nobody has looked yet — and the map has no way to say which. A timestamp rather than a
    # flag because the answer can go stale: a run predating a newly added summit may have
    # missed the parent that summit would have been.
    slope_computed = models.DateTimeField(null=True, blank=True,
                                          help_text='When the slope parent was last computed')
    horizon_computed = models.DateTimeField(null=True, blank=True,
                                            help_text='When the horizon parent was last computed')
    horizon_std_computed = models.DateTimeField(
        null=True, blank=True,
        help_text='When the horizon parent (std) was last computed')

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
        # Prefer the `isolation` annotation from with_isolation(): serializing a few thousand
        # summits otherwise runs a geodesic apiece for a number SQL has already produced.
        annotated = getattr(self, 'isolation', None)
        if annotated is not None:
            return annotated
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

    def _distance_m(self, other_point, annotation=None):
        """
        Metres from this summit to another point, preferring `annotation` — a `Distance` a
        with_* method has already computed in SQL — over a geodesic in Python. Every popup
        section that quotes a distance goes through here.
        """
        if annotation:
            measure = getattr(self, annotation, None)
            if measure is not None:
                return measure.m
        if not (self.point and self.point.location and other_point and other_point.location):
            return None
        return distance(
            (self.point.location.y, self.point.location.x),
            (other_point.location.y, other_point.location.x),
        ).m

    def compute_slope_to_key_col(self, key_col_distance=None):
        """
        Gradient from the summit down to its key col — negative, the col being the lower of
        the two, unlike the slope to a parent. Named `compute_…` so it does
        not collide with the `slope_to_key_col` annotation, which would shadow it. Callers
        that already hold the distance in metres pass it in rather than pay for a second
        geodesic.
        """
        annotated = getattr(self, 'slope_to_key_col', None)
        if annotated is not None:
            return annotated
        if not (self.key_col and self.key_col.point and self.point):
            return None
        dist_m = (key_col_distance if key_col_distance is not None
                  else self._distance_m(self.key_col.point, 'distance_to_key_col'))
        if not dist_m:
            return None
        return (self.key_col.point.altitude - self.point.altitude) / dist_m

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

    def parent_dict(self):
        """
        The prominence parent as the map popup needs it — the way *up*, so `rise` and `slope`
        are positive, mirroring the negative pair in `key_col_dict()`.
        """
        if not (self.prominence_parent and self.prominence_parent.point and self.point):
            return None
        dist_m = self._distance_m(self.prominence_parent.point, 'distance_to_parent')
        rise = self.prominence_parent.point.altitude - self.point.altitude
        return {
            'pk': self.prominence_parent_id,
            'name': self.prominence_parent.point.name,
            'alt': self.prominence_parent.point.altitude,
            'dist': dist_m,
            'rise': rise,
            'slope': rise / dist_m if dist_m else None,
        }

    def nhn_dict(self):
        """
        The nearest higher neighbour: the summit the nearest higher *ground* belongs to. Its
        distance is peak to peak, which is not the isolation — that is measured to the ground
        itself and is reported in `ilp`.
        """
        if not (self.isolation_parent and self.isolation_parent.point and self.point):
            return None
        return {
            'pk': self.isolation_parent_id,
            'name': self.isolation_parent.point.name,
            'alt': self.isolation_parent.point.altitude,
            'dist': self._distance_m(self.isolation_parent.point),
        }

    def slope_parent_dict(self):
        """
        The slope parent and the climb to it. `dd`, `dh` and `slope` are the annotation names
        `with_slope_parent()` uses, and all three are read from there when present.
        """
        if not (self.slope_parent and self.slope_parent.point and self.point):
            return None
        dist_m = self._distance_m(self.slope_parent.point, 'dd')
        rise = getattr(self, 'dh', None)
        if rise is None:
            rise = self.slope_parent.point.altitude - self.point.altitude
        slope = getattr(self, 'slope', None)
        if slope is None:
            slope = rise / dist_m if dist_m else None
        return {
            'pk': self.slope_parent_id,
            'name': self.slope_parent.point.name,
            'alt': self.slope_parent.point.altitude,
            'dist': dist_m,
            'rise': rise,
            'slope': slope,
        }

    def horizon_parent_dict(self):
        """
        The horizon parent and its angle above the horizon — radians, refraction-free, as
        both the `angle` annotation and `angle_to_horizon_parent()` compute it.
        """
        if not (self.horizon_parent and self.horizon_parent.point and self.point):
            return None
        angle = getattr(self, 'angle', None)
        if angle is None:
            angle = self.angle_to_horizon_parent()
        return {
            'pk': self.horizon_parent_id,
            'name': self.horizon_parent.point.name,
            'alt': self.horizon_parent.point.altitude,
            'dist': self._distance_m(self.horizon_parent.point, 'distance_to_horizon'),
            'angle': angle,
        }

    def key_col_dict(self):
        """
        The key col as the map popup needs it — name, altitude, and the three numbers that
        describe the way down to it. `kc` stays the id the client joins on.
        """
        if not (self.key_col and self.key_col.point and self.point):
            return None
        dist_m = self._distance_m(self.key_col.point, 'distance_to_key_col')
        return {
            'pk': self.key_col_id,
            'name': self.key_col.point.name,
            'alt': self.key_col.point.altitude,
            'dist': dist_m,
            'drop': self.point.altitude - self.key_col.point.altitude,
            'slope': self.compute_slope_to_key_col(dist_m),
        }

    def to_dict(self):
        prominence = self.compute_prominence()
        isolation = self.compute_isolation()
        return {
            'pk': self.pk,
            'name': self.point.name if self.point else None,
            'countries': [c.code for c in self.point.countries.all()] if self.point else [],
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
                'parent': self.isolation_parent.point.name if self.isolation_parent else None,
                'dist': isolation.m if isolation is not None else None,
                'lat': self.nearest_higher_point.y if self.nearest_higher_point else None,
                'lon': self.nearest_higher_point.x if self.nearest_higher_point else None,
            },
            'kc': self.key_col_id,
            'key_col': self.key_col_dict(),
            'parent': self.parent_dict(),
        }

    def to_detail_dict(self):
        """
        The half of `to_dict()` that only the popup reads: no geometry and no parent ids,
        those are in the skeleton feature the client already holds. Merged onto that feature
        when the viewport detail arrives, which is why the keys must not drift from
        `to_dict()`.
        """
        isolation = self.compute_isolation()
        return {
            'name': self.point.name if self.point else None,
            'alt': self.point.altitude if self.point else None,
            'countries': [c.code for c in self.point.countries.all()] if self.point else [],
            'key_col': self.key_col_dict(),
            'parent': self.parent_dict(),
            'nhn': self.nhn_dict(),
            # Not `slope_parent` / `horizon_parent`: those keys are the parent ids in the
            # skeleton, and the client joins its lineage lines on them.
            'slope': self.slope_parent_dict(),
            'horizon': self.horizon_parent_dict(),
            'ilp': {
                'name': self.isolation_name,
                'parent': self.isolation_parent.point.name if self.isolation_parent else None,
                'dist': isolation.m if isolation is not None else None,
                'lat': self.nearest_higher_point.y if self.nearest_higher_point else None,
                'lon': self.nearest_higher_point.x if self.nearest_higher_point else None,
            },
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
            ).prefetch_related('point__countries').get(pk=current.prominence_parent_id)
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
            ).prefetch_related('point__countries').get(pk=current.isolation_parent_id)
            ancestors.append(current.to_dict())
        return ancestors

    def prominence_children_list(self):
        return [
            c.to_dict()
            for c in Summit.objects.select_related(
                'point',
                'key_col__point',
            ).prefetch_related('point__countries').filter(prominence_parent=self)
        ]

    def isolation_children_list(self):
        return [
            c.to_dict()
            for c in Summit.objects.select_related(
                'point',
            ).prefetch_related('point__countries').filter(isolation_parent=self)
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
        # `{% object_link summit 'mountain' attr='name' %}` reads this, as do the trees.
        return self.point.display_name()
