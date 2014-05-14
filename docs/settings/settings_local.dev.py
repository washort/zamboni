from mkt.settings import *

SITE_URL = "https://localhost:8000"
STATIC_URL = SITE_URL + '/'
HOSTNAME='localhost'

DEBUG = True
TEMPLATE_DEBUG = DEBUG
DEBUG_PROPAGATE_EXCEPTIONS = DEBUG

# These apps are great during development.
INSTALLED_APPS += (
    'debug_toolbar',
    'django_extensions',
    'fixture_magic',
    'django_qunit',
)

# You want one of the caching backends.  Dummy won't do any caching, locmem is
# cleared every time you restart the server, and memcached is what we run in
# production.
#CACHES = {
#    'default': {
#        'BACKEND': 'django.core.cache.backends.memcached.Memcached',
#        'LOCATION': 'localhost:11211',
#    }
#}
# Here we use the LocMemCache backend from cache-machine, as it interprets the
# "0" timeout parameter of ``cache``  in the same way as the Memcached backend:
# as infinity. Django's LocMemCache backend interprets it as a "0 seconds"
# timeout (and thus doesn't cache at all).
CACHES = {
    'default': {
        'BACKEND': 'caching.backends.locmem.LocMemCache',
        'LOCATION': 'zamboni',
    }
}
# Caching is required for CSRF to work, please do not use the dummy cache.

DATABASES = {
    'default': {
        'NAME': 'zamboni',
        'ENGINE': 'django.db.backends.mysql',
        'USER': 'root',
        'PASSWORD': '',
        'OPTIONS': {'init_command': 'SET storage_engine=InnoDB'},
        'TEST_CHARSET': 'utf8',
        'TEST_COLLATION': 'utf8_general_ci',
    },
}

# Skip indexing ES to speed things up?
SKIP_SEARCH_INDEX = False

LOG_LEVEL = logging.DEBUG
HAS_SYSLOG = False

# For debug toolbar.
if DEBUG:
    INTERNAL_IPS = ('127.0.0.1',)
    MIDDLEWARE_CLASSES += ('debug_toolbar.middleware.DebugToolbarMiddleware',)
    DEBUG_TOOLBAR_CONFIG = {
        'HIDE_DJANGO_SQL': False,
        'INTERCEPT_REDIRECTS': False,
    }

# If you're not running on SSL you'll want this to be False.
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_DOMAIN = None

# Run tasks immediately, don't try using the queue.
CELERY_ALWAYS_EAGER = True

# Disables custom routing in settings.py so that tasks actually run.
CELERY_ROUTES = {}

# Disable timeout code during development because it uses the signal module
# which can only run in the main thread. Celery uses threads in dev.
VALIDATOR_TIMEOUT = -1

# The user id to use when logging in tasks. You should set this to a user that
# exists in your site.
# TASK_USER_ID = 1

WEBAPPS_RECEIPT_KEY = os.path.join(ROOT, 'mkt/webapps/tests/sample.key')

# If you want to allow self-reviews for add-ons/apps, then enable this.
# In production we do not want to allow this.
ALLOW_SELF_REVIEWS = True

# For Marketplace payments.
APP_PURCHASE_KEY = 'localhost'
APP_PURCHASE_AUD = 'localhost'
APP_PURCHASE_TYP = 'mozilla-local/payments/pay/v1'
APP_PURCHASE_SECRET = 'This secret must match your webpay SECRET'

# If you want to skip pre-generation locally, disable it:
PRE_GENERATE_APKS = False

# Assuming you did `npm install` (and not `-g`) like you were supposed to,
# this will be the path to the `stylus` and `lessc` executables.
STYLUS_BIN = path('node_modules/stylus/bin/stylus')
LESS_BIN = path('node_modules/less/bin/lessc')

# Locally we typically don't run more than 1 elasticsearch node. So we set
# replicas to zero.
ES_DEFAULT_NUM_REPLICAS = 0
