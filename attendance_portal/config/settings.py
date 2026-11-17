import os
from pathlib import Path
import os
from django.contrib.messages import constants as message_constants

# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-development-only-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
if not DEBUG and SECRET_KEY == "local-development-only-key":
    raise RuntimeError("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=false.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal.apps.PortalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "portal.views.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.attendance_widget",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.sqlite3",
#         "NAME": BASE_DIR / "db.sqlite3",
#     }
# }

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'attendance_portal',  # Replace with your database name
        'USER': 'postgres',  # Replace with your database username
        'PASSWORD':  'test@123',
        'HOST': 'localhost',  # Replace with your database password
        # 'HOST': '192.168.1.106',  # Use the host of your database server
        'PORT': '5432',  # Default PostgreSQL port
    }
}


AUTH_USER_MODEL = "portal.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"



# ============================================================
# LOAD .ENV FILE
# ============================================================

# The .env file should be next to manage.py.

_env_file = BASE_DIR / ".env"

if _env_file.exists():

    for _line in _env_file.read_text(
        encoding="utf-8-sig"
    ).splitlines():

        _line = _line.strip()

        if (
            not _line
            or _line.startswith("#")
            or "=" not in _line
        ):
            continue

        _key, _value = _line.split("=", 1)

        os.environ[_key.strip()] = (
            _value.strip().strip("\"'")
        )


# ============================================================
# LOGIN / LOGOUT
# ============================================================

LOGIN_URL = "login"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "login"


# ============================================================
# GMAIL SMTP
# ============================================================

# Use a Google App Password.
# Do NOT use your normal Gmail password.

EMAIL_HOST_USER = os.environ.get(
    "EMAIL_HOST_USER",
    ""
)

EMAIL_HOST_PASSWORD = os.environ.get(
    "EMAIL_HOST_PASSWORD",
    ""
)


if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:

    EMAIL_BACKEND = (
        "django.core.mail.backends.smtp.EmailBackend"
    )

    EMAIL_HOST = "smtp.gmail.com"

    EMAIL_PORT = 587

    EMAIL_USE_TLS = True

else:

    # Development fallback.
    # OTP will be printed in the Django terminal.
    EMAIL_BACKEND = (
        "django.core.mail.backends.console.EmailBackend"
    )


DEFAULT_FROM_EMAIL = (
    EMAIL_HOST_USER
    or "noreply@example.com"
)


# ============================================================
# SMS / MOBILE OTP
# ============================================================

# Options:
#
# console
# fast2sms
# twilio
#
# console:
#     OTP is printed in terminal.
#
# fast2sms:
#     Sends SMS to Indian numbers.
#
# twilio:
#     Sends SMS through Twilio.



TWILIO_FROM_NUMBER = os.environ.get(
    "TWILIO_FROM_NUMBER",
    ""
)


# ============================================================
# OTP SETTINGS
# ============================================================

# OTP contains 6 digits.

OTP_LENGTH = 6

# OTP remains valid for 5 minutes.

OTP_EXPIRY_MINUTES = 5

# Maximum wrong OTP attempts.

OTP_MAX_ATTEMPTS = 5

# User must wait 60 seconds before resend.

OTP_RESEND_SECONDS = 60


# ============================================================
# LOGGING
# ============================================================

# LOGGING = {
#     "version": 1,

#     "disable_existing_loggers": False,

#     "handlers": {
#         "console": {
#             "class": "logging.StreamHandler",
#         },
#     },

#     "loggers": {
#         "accounts": {
#             "handlers": ["console"],
#             "level": "INFO",
#         },
#     },
# }


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Bootstrap alert class names (Django uses "error", Bootstrap expects "danger").
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

# Office hours used by the dashboard to flag late arrivals.
OFFICE_START_TIME = os.environ.get("OFFICE_START_TIME", "09:30")  # HH:MM, local time
OFFICE_LATE_GRACE_MINUTES = int(os.environ.get("OFFICE_LATE_GRACE_MINUTES", "15"))
