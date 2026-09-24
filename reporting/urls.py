from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:kind>/", views.detail, name="detail"),
    path("<slug:kind>/print/", views.print_view, name="print"),
    path("<slug:kind>/export/<slug:fmt>/", views.export, name="export"),
]
