from django.urls import path
from . import views

app_name = "timetabling"
urlpatterns = [
    path("", views.RecordList.as_view(), name="schedules"),
    path("schedules/<int:pk>/", views.ScheduleDetail.as_view(), name="schedules-detail"),
    path("schedules/<int:pk>/timetable/", views.ScheduleDetail.as_view(mode="timetable"), name="timetable"),
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
