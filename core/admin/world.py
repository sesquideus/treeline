from django.contrib import admin
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
