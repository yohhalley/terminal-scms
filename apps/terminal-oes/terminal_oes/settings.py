import os
from pathlib import Path
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def database_config():
    url = os.getenv("DATABASE_URL")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": 60,
    }


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "terminal-oes-local-development-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [value.strip() for value in os.getenv(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"
).split(",") if value.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "execution",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "terminal_oes.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "terminal_oes.wsgi.application"
ASGI_APPLICATION = "terminal_oes.asgi.application"

DATABASES = {"default": database_config()}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/equipment/"
LOGOUT_REDIRECT_URL = "/login/"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["execution.permissions.HasOESServiceToken"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

OES_API_TOKEN = os.getenv("OES_API_TOKEN", "")
SIMULATOR_RUNNER_TOKEN = os.getenv("SIMULATOR_RUNNER_TOKEN", "")
SIMULATOR_OES_URL = os.getenv("SIMULATOR_OES_URL", "http://terminal-oes:8000")
SIMULATION_BOOTSTRAP_TOKEN_TTL_SECONDS = int(
    os.getenv("SIMULATION_BOOTSTRAP_TOKEN_TTL_SECONDS", "300")
)
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "terminal-oes")
MQTT_TLS = env_bool("MQTT_TLS", False)
SPARKPLUG_HOST_ID = os.getenv("SPARKPLUG_HOST_ID", "TERMINAL-OES")
SITE_UNS_ROOT = os.getenv("SITE_UNS_ROOT", "scms/v2").strip("/")
MINITOS_URL = os.getenv("MINITOS_URL", "http://localhost:8000")
MINITOS_OES_TOKEN = os.getenv("MINITOS_OES_TOKEN", OES_API_TOKEN)
MINITOS_HTTP_TIMEOUT_SECONDS = float(os.getenv("MINITOS_HTTP_TIMEOUT_SECONDS", "10"))
EQUIPMENT_STALE_SECONDS = int(os.getenv("EQUIPMENT_STALE_SECONDS", "60"))
EQUIPMENT_COMMAND_TTL_SECONDS = int(os.getenv("EQUIPMENT_COMMAND_TTL_SECONDS", "30"))
