from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

def _env(name, default):
    return os.environ.get(name, default)


def _load_env_file(path):
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / '.env')

SECRET_KEY = _env('DJANGO_SECRET_KEY', 'django-insecure-servicetool-smm-reseller-dev-key')

DEBUG = _env('DJANGO_DEBUG', 'True').strip().lower() not in ('0', 'false', 'no', 'off')

ALLOWED_HOSTS = [h.strip() for h in _env('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]

# CSRF por HTTPS/domínio: o Django 4+ rejeita o POST (403) quando a origem não
# consta explicitamente aqui. Se o site é aberto por "https://meudominio",
# essa origem precisa estar na lista OU o login dá erro de CSRF de imediato.
_CSRF_ORIGINS = set()
for _o in _env('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(','):
    _o = _o.strip()
    if _o:
        _CSRF_ORIGINS.add(_o)
_site_url = _env('DJANGO_SITE_URL', 'http://127.0.0.1:8000').strip()
if not _site_url:
    _site_url = 'http://127.0.0.1:8000'
# Se SITE_URL for https, libera também a variação http (caso ainda navegue
# sem TLS no mesmo host) e vice-versa — evita 403 ao alternar o esquema.
from urllib.parse import urlsplit
_parsed = urlsplit(_site_url)
_netloc = _parsed.netloc or _site_url
_CSRF_ORIGINS.update({_site_url})
if _site_url.lower().startswith('https://'):
    _CSRF_ORIGINS.add('http://' + _netloc)
else:
    _CSRF_ORIGINS.add('https://' + _netloc)
# Libera também todos os ALLOWED_HOSTS (www, subdomínios etc.) em https e http
# para evitar 403 de CSRF quando o celular abre o site com/sem www ou troca o esquema.
for _host in ALLOWED_HOSTS:
    _host = _host.strip()
    if not _host or _host == '*':
        continue
    if '://' not in _host:
        _CSRF_ORIGINS.add('https://' + _host)
        _CSRF_ORIGINS.add('http://' + _host)
# Localhost/IP de dev sempre ok.
_CSRF_ORIGINS.update({'http://localhost', 'http://127.0.0.1'})
CSRF_TRUSTED_ORIGINS = sorted(_CSRF_ORIGINS)
del _o, _site_url, _netloc, _parsed, _host, _CSRF_ORIGINS

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'axes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'axes.middleware.AxesMiddleware',
    'core.middleware.MaintenanceModeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'servicetool.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_context',
                'core.context_processors.admin_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'servicetool.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': _env('DJANGO_DB_NAME', str(BASE_DIR / 'db.sqlite3')),
    }
}

AUTH_USER_MODEL = 'core.User'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 2   # horas de bloqueio
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = ['ip_address']
AXES_VERBOSE = False

def _axes_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '') or ''
    if xff:
        return xff.strip().split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''

AXES_CLIENT_IP_CALLABLE = _axes_client_ip

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

LOGIN_URL = '/'
LOGIN_REDIRECT_URL = '/'

SITE_URL = _env('DJANGO_SITE_URL', 'http://127.0.0.1:8000')
