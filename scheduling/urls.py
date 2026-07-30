from django.urls import path

from .views import BlockTimetableView, ConflictDashboardView, FacultyTimetableView


app_name = "scheduling"

urlpatterns = [
    path("", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("dashboard/", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("blocks/<int:block_id>/timetable/", BlockTimetableView.as_view(), name="block-timetable"),
    path("faculty/<int:faculty_id>/timetable/", FacultyTimetableView.as_view(), name="faculty-timetable"),
]
