from collections import OrderedDict
from mkt.constants.applications import DEVICE_LOOKUP
from mkt.constants.features import APP_FEATURES
from mkt.webapps.models import AppFeaturesBase


class MemoryAppFeatures(AppFeaturesBase):
    features = OrderedDict([('has_' + k.lower(), False) for k in APP_FEATURES])

    def __init__(self, fields):
        self.id = 42
        self.created = 'now'
        self.modified = 'now'
        self.fields = self.features.copy()
        self.fields.update(fields)

    def update(self, **d):
        self.fields.update(**d)

    def _fields(self):
        return self.fields.keys()


for k in APP_FEATURES:
    kk = 'has_' + k.lower()
    setattr(MemoryAppFeatures, kk,
            property(
                lambda self, k=kk: self.fields.get(k),
                lambda self, v, k=kk: self.fields[k].__setitem__(v)))


class MemoryTranslation(object):
    def __init__(self, translations):
        self.translations = translations

    def fetch_all_translations(self):
        return self.translations

    def __unicode__(self):
        return self.translations.get['en']

class MemoryVersion(object):
    version = '1.0'
    releasenotes = MemoryTranslation({'en': 'v 1.0'})
    pk = 42

class MemoryWebapp(object):
    def __init__(self, **kw):
        self._features = MemoryAppFeatures({})
        self._users = []
        self.all_previews = []
        self.app_slug = 'fake-app'
        self.app_type = 1
        self.average_rating = 2.5
        self.categories = ['books', 'business']
        self.created = None
        self.current_version = MemoryVersion()
        self.default_locale = None
        self.description = MemoryTranslation({'en': u'Fake Description'})
        self.developer_name = u'Fake Name'
        self.device_types = [DEVICE_LOOKUP['firefoxos'],
                             DEVICE_LOOKUP['desktop']]
        self.file_size = 42
        self.geodata = {'banner_message':
                        MemoryTranslation({'en': u'Banner Msg'}),
                        'banner_regions_slugs': {}}
        self.homepage = None
        self.is_disabled = False
        self.is_offline = False
        self.is_packaged = False
        self.last_updated = None
        self.modified = None
        self.name = MemoryTranslation({'en': u'Fake App'})
        self.pk = kw.get('id') or 17
        self.premium_type = None
        self.public_stats = False
        self.status = 4
        self.support_email = MemoryTranslation({'en': u'support@example.com'})
        self.support_url = MemoryTranslation({'en': u'http://example.com/'})
        self.tags = []
        self.total_reviews = 0
        self.upsell = False
        self.upsold = None
        self.versions = [self.current_version]
        self.__dict__.update(kw)

    def get_content_ratings_by_body(self):
        return {'0': {u'body': 3, 'rating': 0}}

    def get_icon_url(self, size):
        return ''

    def get_manifest_url(self):
        return ''

    def get_package_path(self):
        return None

    def get_regions(self):
        return []

    def get_tags(self):
        return self.tags

    def has_premium(self):
        return False

    def is_premium(self):
        self.is_premium = False

    def get_all_versions(self):
        return self.versions

    def update(self, **kw):
        self.__dict__.update(**kw)

    def update_price(self, price):
        pass


class MemoryStore(object):
    class DoesNotExist(Exception):
        pass

    def __init__(self, apps):
        self.apps = apps

    def apps_installed_for(self, user):
        pass

    def apps_created_by(self, user):
        pass

    def user_relevant_apps(self, user):
        pass

    def uninstall_app(self, user, pk):
        pass

    def get_app(self, pk=None, slug=None, region=None):
        if pk is not None:
            try:
                return self.apps[pk]
            except KeyError:
                raise DoesNotExist()

    def get_account(self, pk=None, email=None, uid=None):
        pass

    def get_anonymous_account(self):
        pass

    def create_account(self, **kwargs):
        pass

    def get_upload(self, uuid):
        pass

    def create_app_from_upload(self, upload, user, is_packaged):
        pass

    def remove_tag(self, app, tag_text):
        pass

    def feed_get(self, region, carrier, original_region):
        pass

    def fetch_feed_elements(self, feed_items):
        pass

    def fetch_app_map(self, request, app_ids, filter_backends):
        pass

    def search_feed(self, q):
        pass

    def fetch_single_feed_element(self, item_type, slug):
        pass

    def fetch_recent_feed_elements(self, index):
        pass


store = MemoryStore({})

