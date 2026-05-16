from cairn.models import AdminModel


class GeoJsonMixin:
    def to_geojson(self):
        raise NotImplementedError


class GeoModel(GeoJsonMixin, AdminModel):
    class Meta:
        abstract = True