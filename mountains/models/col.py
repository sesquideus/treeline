from django.apps import apps
from django.db import models
from django.db.models import Prefetch, F, Value, CharField
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse

from mountains.models.base import GeoModel


class ColQuerySet(models.QuerySet):
    def with_siblings(self):
        return self.prefetch_related('key_for__prominence_children__key_col')

    def with_point(self):
        return self.select_related('point')

    def with_minor(self):
        Summit = apps.get_model('mountains', 'Summit')
        return self.annotate(
            prominence=F('key_for__point__altitude') - F('key_for__key_col__point__altitude')
        ).prefetch_related(
            Prefetch('key_for',
                     queryset=Summit.objects.with_prominence()
            )
        )

    def with_river(self):
        return self.select_related('confluence_river__source', 'confluence_river__parent__source')

    def with_countries(self):
        return self.prefetch_related('point__countries')

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
    point = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=False, related_name='col')
    confluence = models.ForeignKey('Confluence', on_delete=models.CASCADE, null=True, blank=True, related_name='cols')
    confluence_river = models.ForeignKey('River', on_delete=models.CASCADE, null=True, blank=True, related_name='cols')

    objects = ColQuerySet.as_manager()

    def __str__(self):
        if self.point.name:
            return f"{self.point} ({self.point.altitude})"
        elif len(self.key_for.all()) > 0:
            return f"unnamed (for {self.key_for.all()[0].point})"
        else:
            return f"unnamed"

    def name(self):
        if self.point.name:
            return f"{self.point.name}"
        elif len(self.key_for.all()) > 0:
            return f"unnamed (for {self.key_for.all()[0].point})"
        else:
            return f"unnamed"

    def get_absolute_url(self):
        return reverse('col', kwargs={'pk': self.pk})

    def to_dict(self):
        return {
            'pk': self.pk,
            'name': self.point.name if self.point else None,
            'lat': self.point.location.y if self.point else None,
            'lon': self.point.location.x if self.point else None,
            'alt': self.point.altitude if self.point else None,
        } if self.point else None

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
                **self.to_dict(),
                'type': 'col',
                'confluence': {
                    'lon': self.confluence_river.mouth.x,
                    'lat': self.confluence_river.mouth.y,
                } if self.confluence_river and self.confluence_river.mouth else None,
            },
        }