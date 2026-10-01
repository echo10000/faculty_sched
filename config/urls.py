from django.contrib import admin
from django.urls import include, path

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", views.LandingView.as_view(), name="landing"),
    path("dashboard/", views.DashboardView.as_view(), name="home"),
    path("colleges/", views.CollegeListView.as_view(), name="college-list"),
    path("colleges/<int:pk>/", views.CollegeDetailView.as_view(), name="college-detail"),
    path("departments/", views.DepartmentListView.as_view(), name="department-list"),
    path("departments/<int:pk>/", views.DepartmentDetailView.as_view(), name="department-detail"),
    path("academic-calendar/", views.CalendarView.as_view(), name="academic-calendar"),
    path("faculty/", include("faculty.management_urls")),
    path("my-teaching/", include("faculty.portal_urls")),
    path("subjects/", include("resources.subject_urls")),
    path("rooms/", include("resources.room_urls")),
    path("workloads/", include("workloads.urls")),
    path("timetables/", include("timetabling.urls")),
    path("reports/", include("reporting.urls")),
]
# Legacy workload assignment and scheduling URLs remain unmounted.
