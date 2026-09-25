from pathlib import Path

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me-in-production')
DEBUG = env('DEBUG')

ALLOWED_HOSTS = [h.strip() for h in env('ALLOWED_HOSTS', default='*').split(',') if h.strip()]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env('CSRF_TRUSTED_ORIGINS', default='').split(',') if o.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'chat',
    'attribution',
    'analytics',
    'ai',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASE_URL = env('DATABASE_URL', default='')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=not DEBUG),
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Railway / proxy SSL
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# CORS — disabled. The iOS client is a native URLSession caller: it sends no
# Origin header and CORS never applies to it. Allowing all origins only ever
# granted arbitrary websites the ability to call these endpoints from a
# visitor's browser.
CORS_ALLOW_ALL_ORIGINS = False

# DRF
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ] if not DEBUG else [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}

# Telegram
TELEGRAM_BOT_TOKEN = env('TELEGRAM_BOT_TOKEN', default='')
TELEGRAM_OWNER_CHAT_ID = env('TELEGRAM_OWNER_CHAT_ID', default='')
TELEGRAM_WEBHOOK_SECRET = env('TELEGRAM_WEBHOOK_SECRET', default='')

# RevenueCat REST API — secret key, used by the attribution gateway.
# Read-only access to subscriber data. Get from RC dashboard:
# Project settings → API keys → New secret API key.
REVENUECAT_SECRET_API_KEY = env('REVENUECAT_SECRET_API_KEY', default='')

# Analytics aggregation app — shared-secret tokens for the refresh
# trigger endpoint and the RevenueCat webhook receiver. Actual values
# are set in the Railway environment.
ANALYTICS_REFRESH_TOKEN = env('ANALYTICS_REFRESH_TOKEN', default='')
REVENUECAT_WEBHOOK_SECRET = env('REVENUECAT_WEBHOOK_SECRET', default='')

# Apple Ads (Apple Search Ads) API — OAuth credentials for the Campaign
# Management API v5. ASA_PRIVATE_KEY is the full multiline PEM.
ASA_PRIVATE_KEY = env('ASA_PRIVATE_KEY', default='')
ASA_CLIENT_ID = env('ASA_CLIENT_ID', default='')
ASA_TEAM_ID = env('ASA_TEAM_ID', default='')
ASA_KEY_ID = env('ASA_KEY_ID', default='')
# Numeric org ID, sent in the X-AP-Context header on every ASA API call.
ASA_ORG_ID = env('ASA_ORG_ID', default='')

# Anthropic proxy (ai app). The API key lives ONLY here, server-side — the
# iOS client no longer ships one. AI_MODEL is a constant on purpose: it is
# never read from a request.
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY', default='')
AI_ENABLED = env.bool('AI_ENABLED', default=True)  # kill switch
AI_MODEL = 'claude-haiku-4-5-20251001'

# Quotas. Per-user first, then two global ceilings that bound the worst-case
# daily spend even if every per-user check is somehow bypassed.
AI_FREE_LIFETIME_LIMIT = env.int('AI_FREE_LIFETIME_LIMIT', default=3)
AI_PAID_DAILY_LIMIT = env.int('AI_PAID_DAILY_LIMIT', default=100)
AI_IP_HOURLY_LIMIT = env.int('AI_IP_HOURLY_LIMIT', default=30)
AI_GLOBAL_FREE_DAILY_LIMIT = env.int('AI_GLOBAL_FREE_DAILY_LIMIT', default=300)
AI_GLOBAL_DAILY_LIMIT = env.int('AI_GLOBAL_DAILY_LIMIT', default=2000)

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'chat': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'analytics': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ai': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
