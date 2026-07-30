from django.urls import path

from .views import FacultyDashboardView


app_name = "faculty"

urlpatterns = [
    path("dashboard/", FacultyDashboardView.as_view(), name="dashboard"),
]
