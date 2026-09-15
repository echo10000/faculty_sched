from django.urls import path
from .views import FacultyWorkloadView, TeachingDeleteView, TeachingDetailView, TeachingFormView, TeachingListView

app_name = "workloads"
urlpatterns = [
    path("", TeachingListView.as_view(), name="monitor"),
    path("faculty/<int:pk>/", FacultyWorkloadView.as_view(), name="faculty"),
    path("faculty/<int:faculty_pk>/availability/", TeachingListView.as_view(section="availability"), name="faculty-availability"),
]
for section in ("availability", "offerings", "assignments"):
    urlpatterns += [
        path(f"{section}/", TeachingListView.as_view(section=section), name=section),
        path(f"{section}/add/", TeachingFormView.as_view(section=section), name=f"{section}-add"),
        path(f"{section}/<int:pk>/", TeachingDetailView.as_view(section=section), name=f"{section}-detail"),
        path(f"{section}/<int:pk>/edit/", TeachingFormView.as_view(section=section, action="change"), name=f"{section}-edit"),
    ]
    if section != "offerings":
        urlpatterns.append(path(f"{section}/<int:pk>/delete/", TeachingDeleteView.as_view(section=section), name=f"{section}-delete"))
