from django.contrib import admin

from mountains.models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    model = Source

    list_display = ['name',]