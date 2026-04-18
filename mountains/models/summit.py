from django.core.exceptions import ValidationError
from django.db.models import Q, F
from django.urls import reverse
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
                                         related_name='isolation_for',
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

    def clean(self):
        """
        Suggested by Claude.
        """
        super().clean()
        if self.isolation_parent_id is None and self.isolation_source_id is not None:
            raise ValidationError({
                'isolation_source': 'Source must be null when isolation parent is null.'
            })

    def prominence(self):
        if self.island_high_point:
            return self.point.altitude
        if self.key_col:
            return self.point.altitude - self.key_col.point.altitude
        else:
            return None

    def isolation(self):
        if self.isolation_latitude and self.isolation_longitude:
            return distance(
                (self.point.latitude, self.point.longitude),
                (self.isolation_latitude, self.isolation_longitude)
            )
        return None

    def isolation_offset(self):
        if self.isolation_latitude and self.isolation_longitude and self.isolation_parent:
            return distance(
                (self.isolation_parent.point.latitude, self.isolation_parent.point.longitude),
                (self.isolation_latitude, self.isolation_longitude)
            )
        return None

    def distance_to_key_col(self):
        if self.key_col:
            return distance(
                (self.point.latitude, self.point.longitude),
                (self.key_col.point.latitude, self.key_col.point.longitude)
            )
        return None

    def is_complete(self):
        return self.key_col is not None and self.point is not None and self.isolation_parent is not None and \
            self.isolation_latitude is not None and self.isolation_longitude is not None

    def get_absolute_url(self):
        return reverse('mountain', kwargs={'pk': self.pk})

    def __str__(self):
        return f"{self.point}"