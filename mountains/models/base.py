from cairn.models import AdminModel


class GeoJsonMixin:
    def to_geojson(self):
        """ Return a GeoJSON Feature from this object. """
        raise NotImplementedError


class GeoModel(GeoJsonMixin, AdminModel):
    class Meta:
        abstract = True