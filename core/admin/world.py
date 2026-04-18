from django.contrib import admin

from .base import ModelAdmin

from core.models import Country, Language


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'english_name']


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ['code', 'name', 'english_name', 'full_name']
