from django.contrib import admin

from .models import Block, Room, Term


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "room_type", "capacity", "restricted_to_department")
    list_filter = ("room_type", "restricted_to_department")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "term_name", "is_active")
    list_filter = ("is_active", "term_name")


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("curriculum", "term", "year_level", "section_code", "enrolled_count")
    list_filter = ("term", "year_level", "curriculum__program")
