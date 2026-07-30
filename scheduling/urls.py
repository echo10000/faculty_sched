from django.urls import path

from .views import (
    AutoScheduleSuggestionView,
    ApproveAssignmentsView,
    BlockTimetableView,
    BlockTimetablePdfExportView,
    CommitAutoScheduleSuggestionsView,
    ConflictDashboardView,
    FacultyTimetableView,
    FacultyLoadReportExcelExportView,
    SubmitForApprovalView,
    UnlockAssignmentView,
)


app_name = "scheduling"

urlpatterns = [
    path("", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("dashboard/", ConflictDashboardView.as_view(), name="conflict-dashboard"),
    path("autoschedule/", AutoScheduleSuggestionView.as_view(), name="auto-schedule-suggestions"),
    path("autoschedule/commit/", CommitAutoScheduleSuggestionsView.as_view(), name="commit-auto-schedule-suggestions"),
    path("assignments/submit/", SubmitForApprovalView.as_view(), name="submit-for-approval"),
    path("assignments/approve/", ApproveAssignmentsView.as_view(), name="approve-assignments"),
    path("assignments/<int:assignment_id>/unlock/", UnlockAssignmentView.as_view(), name="unlock-assignment"),
    path("blocks/<int:block_id>/export/pdf/", BlockTimetablePdfExportView.as_view(), name="block-timetable-pdf"),
    path("departments/<int:department_id>/export/load.xlsx/", FacultyLoadReportExcelExportView.as_view(), name="faculty-load-xlsx"),
    path("blocks/<int:block_id>/timetable/", BlockTimetableView.as_view(), name="block-timetable"),
    path("faculty/<int:faculty_id>/timetable/", FacultyTimetableView.as_view(), name="faculty-timetable"),
]
