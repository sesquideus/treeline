import pytz
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Q

from core.models import AdminModel


class User(AdminModel, AbstractUser):
    class Meta:
        db_table = 'auth_user'
        indexes = [models.Index(fields=['username', 'first_name', 'last_name'])]
        constraints = [
            models.CheckConstraint(
                condition=Q(latitude__gte=-90, latitude__lte=90, longitude__gt=-180, longitude__lte=180),
                name='user_coordinates',
            ),
            models.CheckConstraint(
                condition=Q(altitude__gte=-400, altitude__lte=15000),
                name='user_altitude',
            ),
        ]

    objects = UserManager()

    # location = models.ForeignKey(Location, null=True, on_delete=models.PROTECT)
    latitude = models.FloatField(blank=True, null=True, help_text="geographic latitude in degrees, north positive")
    longitude = models.FloatField(blank=True, null=True, help_text="geographic longitude in degrees, east positive")
    altitude = models.FloatField(blank=True, null=True, help_text="altitude in metres, orthometric")
    timezone = models.CharField(null=False, blank=False, default='UTC', max_length=64,
                                choices=zip(pytz.common_timezones, pytz.common_timezones),
                                help_text="official timezone name")

    deferrable = ['password', 'email', 'last_login', 'latitude', 'longitude', 'altitude', 'timezone', 'date_joined']

    def format_location(self):
        return "{lat}°, {lon}°, {alt} m".format(
            lat=f'{self.latitude:.6f}' if self.latitude is not None else '?',
            lon=f'{self.longitude:.6f}' if self.longitude is not None else '?',
            alt=f'{self.altitude:.0f}' if self.altitude is not None else '?',
        )

    # def get_location(self):
    #    return EarthLocation(lat=self.latitude, lon=self.longitude, height=self.altitude)
