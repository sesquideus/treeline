from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.db.models import Prefetch, F, Value, Q
from django.db.models.functions import Concat, Coalesce
from django.urls import reverse

from cairn.models import AdminModel


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
                Coalesce(
                    F('point__name'),
                    Concat(Value('unnamed ('), F('key_for__point__name'), Value(')'))
                ),
                Value(' ('),
                F('point__altitude'),
                Value(')'),
            )
        )

    def with_displacement(self):
        return self.annotate(
            displacement=Distance('source__location', 'mouth'),
        )

    def with_db_status(self):
        return self.annotate(
            complete=Q(source__location__isnull=False) & Q(source__altitude__isnull=False) & \
                     Q(mouth__isnull=False) & Q(mouth_altitude__isnull=False) & \
                     Q(parent__isnull=False) & Q(summit__isnull=False),
        )


class River(AdminModel):
    source = models.OneToOneField('NamedPoint', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    summit = models.ForeignKey('Summit', on_delete=models.CASCADE, null=True, blank=True, related_name='rivers')

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
