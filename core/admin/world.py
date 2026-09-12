from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.forms import PointField

import mapwidgets

from cairn.admin import ModelAdmin

from core.fields import PointFormField
from core.models import Country, Language


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['iso639_1', 'country_code', 'name', 'english_name']


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ['code', 'name', 'english_name', 'full_name']


class PointModelAdmin(ModelAdmin):
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, PointField):
            return PointFormField(label=db_field.verbose_name.title(), required=False)
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class GeometryModelAdmin(ModelAdmin, GISModelAdmin):
    """
    cairn's `list_display` directives plus GeoDjango's OpenLayers widget.

    For models carrying a polygon rather than a point: `PointModelAdmin` above swaps every
    `PointField` for the lat/lon pair, which has no equivalent for an area, so those want the
    map editor instead. cairn's `ModelAdmin` comes first so its `check()` — the one that
    validates the directive syntax, and which `test_system_checks` runs — is the one found;
    `formfield_for_dbfield` is defined only on GeoDjango's side, so the widget still wins.

    The map opens where every other map in this admin does.
    """
    gis_widget_kwargs = {
        'attrs': {'default_lat': 48.5, 'default_lon': 19.5, 'default_zoom': 7},
    }
