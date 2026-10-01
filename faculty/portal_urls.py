from django.urls import path
from . import portal

app_name = 'faculty-portal'
urlpatterns = [
    path('', portal.schedule, name='schedule'),
    path('load/', portal.schedule, {'kind': 'workload'}, name='workload'),
    path('download/<str:kind>/<str:format>/', portal.download, name='download'),
]
