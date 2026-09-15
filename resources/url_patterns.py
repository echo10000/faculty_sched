from django.urls import path
from .views import ResourceDetailView, ResourceFormView, ResourceListView, ResourceStatusView


def patterns(kind):
    return [
        path("", ResourceListView.as_view(kind=kind), name="list"),
        path("add/", ResourceFormView.as_view(kind=kind), name="add"),
        path("<int:pk>/", ResourceDetailView.as_view(kind=kind), name="detail"),
        path("<int:pk>/edit/", ResourceFormView.as_view(kind=kind, action="change"), name="edit"),
        path("<int:pk>/status/", ResourceStatusView.as_view(kind=kind), name="status"),
    ]
