from django.apps import apps
from django.db import models
from django.db.models import Prefetch, F, Value, CharField
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse

from core.functions.world import distance
from mountains.models.base import GeoModel


class ColQuerySet(models.QuerySet):
    def with_siblings(self):
        return self.prefetch_related('key_for__prominence_children__key_col')

    def with_point(self):
        return self.select_related('point')

    def with_minor(self):
        Summit = apps.get_model('mountains', 'Summit')
        return self.annotate(
            depth=F('key_for__point__altitude') - F('key_for__key_col__point__altitude')
        ).prefetch_related(
            Prefetch('key_for',
                     queryset=Summit.objects.with_prominence()
            )
        )

    def with_rivers(self):
        return self.select_related('confluence_river__source', 'confluence_river__parent__source')

    def with_countries(self):
        return self.prefetch_related('point__countries')

    def skeleton_features(self):
        """
        GeoJSON features with only what the global map draws: the col's position, its name
        for a popup caption, and the confluence position the pink line runs to. Everything
        else a col popup shows comes from the viewport detail endpoint.

        Built from `values_list`, like `SummitQuerySet.skeleton_features()`; property names
        must match `to_dict()`, which is what `static/js/map.js` reads.
        """
        rows = self.values_list(
            'pk', 'point__location', 'point__name',
            'confluence_river_id', 'confluence_river__mouth',
        )
        return [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [location.x, location.y]},
                'properties': {
                    'type': 'col',
                    'pk': pk,
                    'name': name,
                    # `river` is the id of the river, which is what makes sister cols
                    # findable in the browser: they are the cols that share it.
                    'confluence': {'lon': mouth.x, 'lat': mouth.y, 'river': river}
                                  if mouth else None,
                },
            }
            for pk, location, name, river, mouth in rows
            if location is not None
        ]

    def with_full_name(self):
        return self.annotate(
            full_name=Concat(
                Coalesce(
                    F('point__name'),
                    Concat(Value('unnamed ('), F('key_for__point__name'), Value(')'))
                ),
                Value(' ('),
                F('point__altitude'),
                Value(')'),
                output_field=CharField()
            )
        )


class Col(GeoModel):
    class Meta:
        ordering = ['point__altitude']

    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False, related_name='col')
    confluence = models.ForeignKey('Confluence', on_delete=models.CASCADE, null=True, blank=True, related_name='cols')
    confluence_river = models.ForeignKey('River', on_delete=models.CASCADE, null=True, blank=True, related_name='cols')

    objects = ColQuerySet.as_manager()

    def __str__(self):
        if self.point.name:
            fragment = f"{self.point.name}"
        elif hasattr(self, 'key_for'):
            fragment = f"unnamed → {self.key_for.point.name}"
        else:
            fragment = f"unnamed col"

        return f"{fragment} ({self.point.altitude}\u00A0m)"

    def name(self):
        if self.point.name:
            return f"{self.point.name}"
        elif hasattr(self, 'key_for'):
            return f"→ {self.key_for.name()}"
        else:
            return f"unnamed col"

    def get_absolute_url(self):
        return reverse('col', kwargs={'pk': self.pk})

    def distance_to_confluence(self):
        """ Geodesic distance from the col down to its confluence (the river mouth) """
        if not (self.point and self.point.location
                and self.confluence_river and self.confluence_river.mouth):
            return None
        return distance(
            (self.point.location.y, self.point.location.x),
            (self.confluence_river.mouth.y, self.confluence_river.mouth.x),
        )

    def to_dict(self):
        confluence_distance = self.distance_to_confluence()
        return {
            'pk': self.pk,
            'name': self.point.name if self.point else None,
            'countries': [c.code for c in self.point.countries.all()] if self.point else [],
            'lat': self.point.location.y if self.point else None,
            'lon': self.point.location.x if self.point else None,
            'alt': self.point.altitude if self.point else None,
            'depth': self.key_for.prominence if hasattr(self, 'key_for') else None,
            'confluence': {
                # The river, as in the skeleton — this used to be the id of its source point.
                'river': self.confluence_river_id,
                'name': self.confluence_river.source.name,
                'lon': self.confluence_river.mouth.x,
                'lat': self.confluence_river.mouth.y,
                'alt': self.confluence_river.mouth_altitude,
                'dist': confluence_distance.m if confluence_distance is not None else None,
            } if self.confluence_river else None,
            'river': {
                'pk': self.confluence_river_id,
                'name': self.confluence_river.name(),
                'parent': self.confluence_river.parent.name() if self.confluence_river.parent else None,
                'parent_pk': self.confluence_river.parent_id,
            } if self.confluence_river else None,
            'key_for': self.key_for.point.name if hasattr(self, 'key_for') else None,
            'key_for_pk': self.key_for.pk if hasattr(self, 'key_for') else None,
            # The higher of the two sides. A key col is the saddle between the peak it
            # belongs to and the higher ground it hangs off — which is that peak's prominence
            # parent, so the pair names both slopes meeting here.
            'major': (self.key_for.prominence_parent.point.name
                      if hasattr(self, 'key_for') and self.key_for.prominence_parent else None),
            'major_pk': (self.key_for.prominence_parent_id
                         if hasattr(self, 'key_for') else None),
        } if self.point else None

    def to_detail_dict(self):
        """
        The popup half of `to_dict()` — no geometry, since the client already holds the
        skeleton feature this is merged onto. `confluence` is repeated in full because the
        popup wants its altitude and distance, and the pink line still needs its position.
        """
        detail = self.to_dict() or {}
        return {key: value for key, value in detail.items() if key not in ('lat', 'lon')}

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
                'type': 'col',
                **self.to_dict(),
            },
        }
