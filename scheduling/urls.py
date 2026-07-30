from django.urls import path

from .views import (
    AutoScheduleSuggestionView,
    BlockTimetableView,
    CommitAutoScheduleSuggestionsView,
    ConflictDashboardView,
    FacultyTimetableView,
)


app_name = "scheduling"

urlpatterns = [
    path("", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("dashboard/", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("autoschedule/", AutoScheduleSuggestionView.as_view(), name="auto-schedule-suggestions"),
    path("autoschedule/commit/", CommitAutoScheduleSuggestionsView.as_view(), name="commit-auto-schedule-suggestions"),
    path("blocks/<int:block_id>/timetable/", BlockTimetableView.as_view(), name="block-timetable"),
    path("faculty/<int:faculty_id>/timetable/", FacultyTimetableView.as_view(), name="faculty-timetable"),
]
