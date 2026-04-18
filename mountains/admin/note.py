from django.contrib import admin

from mountains.models.note import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    model = Note


class NoteInline(admin.TabularInline):
    model = Note
    extra = 1