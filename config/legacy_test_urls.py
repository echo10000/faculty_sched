"""Compatibility URL configuration used only by pre-existing legacy tests."""
from django.urls import include, path
from .urls import urlpatterns as foundation_urls

urlpatterns = [
    path("faculty/", include("faculty.urls")),
    path("scheduling/", include("scheduling.urls")),
] + foundation_urls
