from django.urls import path
from . import views
from . import workflow_views

app_name = "timetabling"
urlpatterns = [
    path("", views.RecordList.as_view(), name="schedules"),
    path("prepare/", views.PrepareScheduleView.as_view(), name="prepare"),
    path("my-schedules/", workflow_views.my_submissions, name="my-schedules"),
    path("review/", workflow_views.review_queue, name="review-queue"),
    path("official/", workflow_views.official_schedules, name="official-schedules"),
    path("schedules/<int:pk>/", views.ScheduleDetail.as_view(), name="schedules-detail"),
    path("schedules/<int:pk>/review/", workflow_views.schedule_review, name="schedule-review"),
    path("schedules/<int:pk>/history/", workflow_views.schedule_history, name="schedule-history"),
    path("schedules/<int:pk>/finalize/", workflow_views.transition, {"action": "finalize"}, name="schedule-finalize"),
    path("schedules/<int:pk>/revise/", workflow_views.transition, {"action": "revise"}, name="schedule-revise"),
    path("schedules/<int:pk>/timetable/", views.ScheduleDetail.as_view(mode="timetable"), name="timetable"),
    path("schedules/<int:pk>/unscheduled/", views.ScheduleDetail.as_view(mode="unscheduled"), name="unscheduled"),
    path("schedules/<int:pk>/conflicts/", views.ScheduleDetail.as_view(mode="conflicts"), name="conflicts"),
    path("schedules/<int:pk>/validate/", views.ValidateView.as_view(), name="validate"),
    path("schedules/<int:schedule_id>/entries/add/", views.EntryEditor.as_view(), name="entries-add"),
    path("schedules/<int:schedule_id>/entries/<int:pk>/edit/", views.EntryEditor.as_view(), name="entries-edit"),
    path("schedules/<int:schedule_id>/entries/<int:pk>/delete/", views.EntryDelete.as_view(), name="entries-delete"),
    path("closures/<int:pk>/delete/", views.ClosureDelete.as_view(), name="closures-delete"),
    path("generator/", views.GeneratorView.as_view(), name="generator"),
    path("generation-runs/", views.GenerationRunList.as_view(), name="generation-runs"),
    path("generation-runs/<int:pk>/", views.GenerationRunDetail.as_view(), name="generation-run-detail"),
    path("generation-runs/<int:pk>/accept/", views.GenerationAcceptView.as_view(), name="generation-run-accept"),
    path("generation-runs/<int:pk>/discard/", views.GenerationDiscardView.as_view(), name="generation-run-discard"),
]
for section in views.SPECS:
    if section != "schedules":
        urlpatterns.append(path(f"{section}/", views.RecordList.as_view(section=section), name=section))
    urlpatterns += [
        path(f"{section}/add/", views.RecordForm.as_view(section=section), name=f"{section}-add"),
        path(f"{section}/<int:pk>/edit/", views.RecordForm.as_view(section=section, action="change"), name=f"{section}-edit"),
    ]
