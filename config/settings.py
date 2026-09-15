"""Environment-driven settings for the Phase 1 foundation."""
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from decouple import AutoConfig
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = AutoConfig(search_path=BASE_DIR)
DEBUG = env("DEBUG", default=False, cast=bool)
SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY or (not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure"))):
    raise ImproperlyConfigured("Set a unique SECRET_KEY (at least 50 characters outside DEBUG mode).")
ALLOWED_HOSTS = [host.strip() for host in env("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in env("CSRF_TRUSTED_ORIGINS", default="").split(",") if origin.strip()]

INSTALLED_APPS = [
    "core.apps.FoundationAdminConfig", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "core.apps.CoreConfig", "academics", "faculty", "scheduling", "accounts", "audit", "resources", "workloads", "timetabling",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "core.context_processors.navigation",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": env("DB_NAME", default="campusload"), "USER": env("DB_USER", default="campusload_app"),
    "PASSWORD": env("DB_PASSWORD", default=""), "HOST": env("DB_HOST", default="127.0.0.1"),
    "PORT": env("DB_PORT", default="5432"), "CONN_MAX_AGE": env("DB_CONN_MAX_AGE", default=0, cast=int),
    "OPTIONS": {"connect_timeout": 5, "sslmode": env("DB_SSLMODE", default="prefer")},
}}
AUTHENTICATION_BACKENDS = ["accounts.backends.ScopedRoleBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=not DEBUG, cast=bool)
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", default=0 if DEBUG else 31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool)
SECURE_HSTS_PRELOAD = False
X_FRAME_OPTIONS = "DENY"
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Manila")
try:
    ZoneInfo(TIME_ZONE)
except ZoneInfoNotFoundError as exc:
    raise ImproperlyConfigured("TIME_ZONE must be a valid IANA timezone.") from exc
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INSTITUTION_NAME = env("INSTITUTION_NAME", default="Negros Oriental State University - Bais Campus")
SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS = env(
    "SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS", default=10, cast=int
)
SCHEDULER_MAX_CANDIDATES = env("SCHEDULER_MAX_CANDIDATES", default=100000, cast=int)
SCHEDULER_MAX_SLOT_LITERALS = env("SCHEDULER_MAX_SLOT_LITERALS", default=2000000, cast=int)
for name in (
    "SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS",
    "SCHEDULER_MAX_CANDIDATES",
    "SCHEDULER_MAX_SLOT_LITERALS",
):
    if globals()[name] <= 0:
        raise ImproperlyConfigured(f"{name} must be a positive integer.")
