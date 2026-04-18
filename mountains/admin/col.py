from django.contrib import admin

from ..models import Col


@admin.register(Col)
class ColAdmin(admin.ModelAdmin):
    list_display = ['point', 'point__latitude', 'point__longitude', 'point__altitude']
    fieldsets = (
        ('Identity', {
            'fields': (
                'point',
            )
        },),
    )
