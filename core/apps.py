from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class FoundationAdminConfig(AdminConfig):
    default_site = "core.admin_site.FoundationAdminSite"


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
