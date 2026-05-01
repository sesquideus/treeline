from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm

from users.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'is_superuser', 'timezone',
                    'get_location', 'get_groups']
    add_form = AdminUserCreationForm
    readonly_fields = ['date_joined', 'last_login']

    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Position', {
            'fields': [
                ('latitude', 'longitude', 'altitude'),
                ('timezone',),
            ],
        }),
    )

    @admin.display(description="Groups")
    def get_groups(self, instance):
        return ', '.join([x.__str__() for x in instance.groups.all()])

    @admin.display(description="Location")
    def get_location(self, instance):
        return instance.format_location()
