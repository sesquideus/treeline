from django.db import models


class VerticalDatum(models.Model):
    """
    What an altitude is measured from.

    The same summit has a different number in Bpv, in the Polish EVRF2007 realisation, in
    EGM2008 or above the ellipsoid — centimetres apart between the levelled systems, some
    forty metres from the ellipsoid. A number without its datum cannot be compared across a
    border, nor with a lidar model, so every `NamedPoint.altitude` names one.

    `crs` is what PROJ needs to take a height in this datum to EGM2008: a compound
    horizontal+vertical CRS, or a 3D geographic one for ellipsoidal heights; where EPSG has no
    route to EGM2008, a PROJ string naming a geoid grid (TWVD 2001 through TWGEOID18h, which
    `install_isg_grid` installs).
    """
    class Kind(models.TextChoices):
        NORMAL = 'normal', 'normal heights (quasigeoid)'
        ORTHOMETRIC = 'orthometric', 'orthometric heights (geoid)'
        ELLIPSOIDAL = 'ellipsoidal', 'ellipsoidal heights'

    class Meta:
        ordering = ['code']

    code = models.CharField(max_length=16, unique=True,
                            help_text='Short label printed next to an altitude: "Bpv", "EGM2008"')
    name = models.CharField(max_length=128)
    crs = models.CharField(max_length=255,
                           help_text='PROJ source CRS for a height in this datum, '
                                     'e.g. "EPSG:4258+8357", or a PROJ string with +geoidgrids')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code
