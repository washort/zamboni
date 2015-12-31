import json
from decimal import Decimal

from django.conf import settings
from django.core.urlresolvers import reverse

import commonware.log
from rest_framework import response, serializers
from django.utils.translation import ungettext as ngettext

import mkt
from mkt.api.fields import (ESTranslationSerializerField, LargeTextField,
                            ReverseChoiceField, SemiSerializerMethodField,
                            TranslationSerializerField)
from mkt.constants.applications import DEVICE_TYPES
from mkt.constants.categories import CATEGORY_CHOICES
from mkt.constants.iarc_mappings import HUMAN_READABLE_DESCS_AND_INTERACTIVES
from mkt.constants.payments import PROVIDER_BANGO
from mkt.features.utils import load_feature_profile
from mkt.prices.models import AddonPremium, Price
from mkt.search.serializers import BaseESSerializer, es_to_datetime
from mkt.site.helpers import absolutify
from mkt.submit.forms import mark_for_rereview
from mkt.submit.serializers import PreviewSerializer, SimplePreviewSerializer
from mkt.tags.models import attach_tags
from mkt.translations.utils import no_translation
from mkt.versions.models import Version
from mkt.webapps.models import (AddonUpsell, AppFeatures, Geodata, Preview,
                                Webapp)
from mkt.webapps.utils import dehydrate_content_rating


log = commonware.log.getLogger('z.api')


def http_error(errorclass, reason, extra_data=None):
    r = errorclass()
    data = {'reason': reason}
    if extra_data:
        data.update(extra_data)
    r.content = json.dumps(data)
    return response.Response(r)


class AppFeaturesSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppFeatures

    def to_representation(self, obj):
        ret = super(AppFeaturesSerializer, self).to_representation(obj)
        ret['required'] = obj.to_list()
        return ret


class RegionSerializer(serializers.Serializer):
    name = serializers.CharField()
    slug = serializers.CharField()
    mcc = serializers.CharField()
    adolescent = serializers.BooleanField()


class BaseAppSerializer(serializers.ModelSerializer):
    # REST Framework 3.x doesn't allow meta.fields to omit fields declared in
    # the class body, but it does allow omitting ones in superclasses. All the
    # serializers are subsets of the full field collection, hence this
    # superclass.
    app_type = serializers.ChoiceField(
        choices=mkt.ADDON_WEBAPP_TYPES_LOOKUP.items(), read_only=True)
    author = serializers.CharField(source='developer_name', read_only=True)
    categories = serializers.ListField(
        child=serializers.ChoiceField(choices=CATEGORY_CHOICES,
                                      read_only=False),
        read_only=False,
        required=True)
    content_ratings = serializers.SerializerMethodField()
    created = serializers.DateTimeField(read_only=True,
                                        format=None)
    current_version = serializers.CharField(source='current_version.version',
                                            read_only=True)
    default_locale = serializers.CharField(read_only=True)
    device_types = SemiSerializerMethodField()
    description = TranslationSerializerField(required=False)
    homepage = TranslationSerializerField(required=False)
    feature_compatibility = serializers.SerializerMethodField()
    file_size = serializers.IntegerField(read_only=True)
    icons = serializers.SerializerMethodField()
    id = serializers.IntegerField(source='pk', required=False)
    is_disabled = serializers.BooleanField(read_only=True)
    is_homescreen = serializers.SerializerMethodField()
    is_offline = serializers.BooleanField(read_only=True)
    is_packaged = serializers.BooleanField(read_only=True)
    last_updated = serializers.DateTimeField(read_only=True,
                                             format=None)
    manifest_url = serializers.CharField(source='get_manifest_url',
                                         read_only=True)
    modified = serializers.DateTimeField(read_only=True,
                                         format=None)
    name = TranslationSerializerField(required=False)
    package_path = serializers.CharField(source='get_package_path',
                                         read_only=True)
    payment_account = serializers.SerializerMethodField()
    payment_required = serializers.SerializerMethodField()
    premium_type = ReverseChoiceField(
        choices_dict=mkt.ADDON_PREMIUM_API, required=False)
    previews = PreviewSerializer(many=True, required=False,
                                 source='all_previews')
    price = SemiSerializerMethodField(source='*', required=False)
    price_locale = serializers.SerializerMethodField()
    privacy_policy = LargeTextField(view_name='app-privacy-policy-detail',
                                    queryset=Webapp.objects,
                                    required=False)
    promo_imgs = serializers.SerializerMethodField()
    public_stats = serializers.BooleanField(read_only=True)
    ratings = serializers.SerializerMethodField('get_ratings_aggregates')
    regions = RegionSerializer(read_only=True, source='get_regions', many=True)
    release_notes = TranslationSerializerField(
        read_only=True,
        source='current_version.releasenotes')
    resource_uri = serializers.HyperlinkedIdentityField(view_name='app-detail')
    slug = serializers.CharField(source='app_slug', required=False)
    status = serializers.IntegerField(read_only=True)
    support_email = TranslationSerializerField(required=False)
    support_url = TranslationSerializerField(required=False)
    supported_locales = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    upsell = serializers.SerializerMethodField()
    upsold = serializers.HyperlinkedRelatedField(
        view_name='app-detail', source='upsold.free',
        required=False, queryset=Webapp.objects.all())
    user = serializers.SerializerMethodField('get_user_info')
    versions = serializers.SerializerMethodField()


class AppSerializer(BaseAppSerializer):

    class Meta:
        model = Webapp
        fields = [
            'app_type', 'author', 'categories', 'content_ratings', 'created',
            'current_version', 'default_locale', 'description', 'device_types',
            'feature_compatibility', 'file_size', 'homepage', 'hosted_url',
            'icons', 'id', 'is_disabled', 'is_homescreen', 'is_offline',
            'is_packaged', 'last_updated', 'manifest_url', 'name',
            'package_path', 'payment_account', 'payment_required',
            'premium_type', 'previews', 'price', 'price_locale',
            'privacy_policy', 'promo_imgs', 'public_stats', 'release_notes',
            'ratings', 'regions', 'resource_uri', 'slug', 'status',
            'support_email', 'support_url', 'supported_locales', 'tags',
            'upsell', 'upsold', 'user', 'versions'
        ]

    def _get_region_id(self):
        request = self.context.get('request')
        REGION = getattr(request, 'REGION', None)
        return REGION.id if REGION else None

    def _get_region_slug(self):
        request = self.context.get('request')
        REGION = getattr(request, 'REGION', None)
        return REGION.slug if REGION else None

    def get_content_ratings(self, app):
        body = mkt.regions.REGION_TO_RATINGS_BODY().get(
            self._get_region_slug(), 'generic')

        return {
            'body': body,
            'rating': app.get_content_ratings_by_body().get(body, None),
            'descriptors': (
                app.rating_descriptors.to_keys_by_body(body)
                if hasattr(app, 'rating_descriptors') else []),
            'descriptors_text': (
                [HUMAN_READABLE_DESCS_AND_INTERACTIVES[key]
                 for key in app.rating_descriptors.to_keys_by_body(body)]
                if hasattr(app, 'rating_descriptors') else []),
            'interactives': (
                app.rating_interactives.to_keys()
                if hasattr(app, 'rating_interactives') else []),
            'interactives_text': (
                [HUMAN_READABLE_DESCS_AND_INTERACTIVES[key] for key in
                 app.rating_interactives.to_keys()]
                if hasattr(app, 'rating_interactives') else []),
        }

    def get_icons(self, app):
        return dict([(icon_size, app.get_icon_url(icon_size))
                     for icon_size in mkt.CONTENT_ICON_SIZES])

    def get_feature_compatibility(self, app):
        request = self.context['request']
        if not hasattr(request, 'feature_profile'):
            load_feature_profile(request)
        if request.feature_profile is None or app.current_version is None:
            # No profile information sent, or we don't have a current version,
            # we can't return compatibility, return null.
            return None
        app_features = app.current_version.features.to_list()
        return request.feature_profile.has_features(app_features)

    def get_payment_account(self, app):
        # Avoid a query for payment_account if the app is not premium.
        if not app.is_premium():
            return None

        try:
            # This is a soon to be deprecated API property that only
            # returns the Bango account for historic compatibility.
            app_acct = app.payment_account(PROVIDER_BANGO)
            return reverse('payment-account-detail',
                           args=[app_acct.payment_account.pk])
        except app.PayAccountDoesNotExist:
            return None

    def get_payment_required(self, app):
        if app.has_premium():
            tier = app.get_tier()
            return bool(tier and tier.price)
        return False

    def get_price(self, app):
        if app.has_premium():
            price = app.get_price(region=self._get_region_id())
            if price is not None:
                return unicode(price)
        return None

    def get_price_locale(self, app):
        if app.has_premium():
            return app.get_price_locale(region=self._get_region_id())
        return None

    def get_promo_imgs(self, obj):
        return dict([(promo_img_size, obj.get_promo_img_url(promo_img_size))
                     for promo_img_size in mkt.PROMO_IMG_SIZES])

    def get_ratings_aggregates(self, app):
        return {'average': app.average_rating,
                'count': app.total_reviews}

    def get_supported_locales(self, app):
        locs = getattr(app.current_version, 'supported_locales', '')
        if locs:
            return locs.split(',') if isinstance(locs, basestring) else locs
        else:
            return []

    def get_tags(self, app):
        if not hasattr(app, 'tags_list'):
            attach_tags([app])
        return getattr(app, 'tags_list', [])

    def get_upsell(self, app):
        upsell = False
        if app.upsell:
            upsell = app.upsell.premium
        # Only return the upsell app if it's public and we are not in an
        # excluded region.
        if (upsell and upsell.is_public() and self._get_region_id()
                not in upsell.get_excluded_region_ids()):
            return {
                'id': upsell.id,
                'app_slug': upsell.app_slug,
                'icon_url': upsell.get_icon_url(128),
                'name': unicode(upsell.name),
                'resource_uri': reverse('app-detail', kwargs={'pk': upsell.pk})
            }
        else:
            return False

    def get_user_info(self, app):
        request = self.context.get('request')
        if request and request.user.is_authenticated():
            user = request.user
            return {
                'developed': app.addonuser_set.filter(
                    user=user, role=mkt.AUTHOR_ROLE_OWNER).exists(),
                'installed': app.has_installed(user),
                'purchased': app.pk in user.purchase_ids(),
            }

    def get_is_homescreen(self, app):
        return app.is_homescreen()

    def get_versions(self, app):
        # Disable transforms, we only need two fields: version and pk.
        # Unfortunately, cache-machine gets in the way so we can't use .only()
        # (.no_transforms() is ignored, defeating the purpose), and we can't
        # use .values() / .values_list() because those aren't cached :(
        return dict((v.version, reverse('version-detail', kwargs={'pk': v.pk}))
                    for v in app.versions.all().no_transforms())

    def validate_categories(self, categories):
        set_categories = set(categories)
        total = len(set_categories)
        max_cat = mkt.MAX_CATEGORIES

        if total > max_cat:
            # L10n: {0} is the number of categories.
            raise serializers.ValidationError(ngettext(
                'You can have only {0} category.',
                'You can have only {0} categories.',
                max_cat).format(max_cat))

        return categories

    def get_device_types(self, device_types):
        with no_translation():
            return [n.api_name for n in device_types]

    def save_device_types(self, obj, new_types):
        new_types = [mkt.DEVICE_LOOKUP[d].id for d in new_types]
        old_types = [x.id for x in obj.device_types]

        added_devices = set(new_types) - set(old_types)
        removed_devices = set(old_types) - set(new_types)

        for d in added_devices:
            obj.addondevicetype_set.create(device_type=d)
        for d in removed_devices:
            obj.addondevicetype_set.filter(device_type=d).delete()

        # Send app to re-review queue if public and new devices are added.
        if added_devices and obj.status in mkt.WEBAPPS_APPROVED_STATUSES:
            mark_for_rereview(obj, added_devices, removed_devices)

    def save_upsold(self, obj, upsold):
        current_upsell = obj.upsold
        if upsold and upsold != obj.upsold.free:
            if not current_upsell:
                log.debug('[1@%s] Creating app upsell' % obj.pk)
                current_upsell = AddonUpsell(premium=obj)
            current_upsell.free = upsold
            current_upsell.save()

        elif current_upsell:
            # We're deleting the upsell.
            log.debug('[1@%s] Deleting the app upsell' % obj.pk)
            current_upsell.delete()

    def save_price(self, obj, price):
        # Only valid for premium apps; don't call this on free ones.
        valid_prices = Price.objects.exclude(
            price='0.00').values_list('price', flat=True)
        if not (price and Decimal(price) in valid_prices):
            raise serializers.ValidationError(
                {'price':
                 ['Premium app specified without a valid price. Price can be'
                  ' one of %s.' % (', '.join('"%s"' % str(p)
                                             for p in valid_prices),)]})
        premium = obj.premium
        if not premium:
            premium = AddonPremium()
            premium.addon = obj
        premium.price = Price.objects.active().get(price=price)
        premium.save()

    def validate_device_types(self, device_types):
        for v in device_types:
            if v not in mkt.DEVICE_LOOKUP.keys():
                raise serializers.ValidationError(
                    str(v) + ' is not one of the available choices.')
        return device_types

    def validate_price(self, price):
        return {'price': price}

    def update(self, instance, attrs):
        extras = []
        # Upsell bits are handled here because we need to remove it
        # from the attrs dict before deserializing.
        upsold = attrs.pop('upsold.free', None)
        if upsold is not None:
            extras.append((self.save_upsold, upsold))
        price = attrs.pop('price', None)
        if attrs.get('premium_type') not in (mkt.ADDON_FREE,
                                             mkt.ADDON_FREE_INAPP):
            extras.append((self.save_price, price))
        device_types = attrs.pop('device_types', None)
        if device_types is not None:
            extras.append((self.save_device_types, device_types))
        if instance:
            instance = super(AppSerializer, self).update(instance, attrs)
        else:
            instance = super(AppSerializer, self).create(attrs)
        for f, v in extras:
            f(instance, v)
        return instance

    def create(self, data):
        return self.update(None, data)


class ESAppSerializer(BaseESSerializer, AppSerializer):
    # Fields specific to search.
    absolute_url = serializers.SerializerMethodField()
    reviewed = serializers.DateTimeField(format=None,
                                         read_only=True)

    # Override previews, because we don't need the full PreviewSerializer.
    previews = SimplePreviewSerializer(many=True, source='all_previews')

    # Override those, because we want a different source. Also, related fields
    # will call self.queryset early if they are not read_only, so force that.
    file_size = serializers.SerializerMethodField()
    is_disabled = serializers.BooleanField(source='_is_disabled',
                                           read_only=True)
    manifest_url = serializers.CharField()
    package_path = serializers.SerializerMethodField()

    # Feed collection.
    group = ESTranslationSerializerField(required=False)

    # The fields we want converted to Python date/datetimes.
    datetime_fields = ('created', 'last_updated', 'modified', 'reviewed')

    class Meta(AppSerializer.Meta):
        fields = AppSerializer.Meta.fields + ['absolute_url', 'group',
                                              'reviewed']

    def __init__(self, *args, **kwargs):
        super(ESAppSerializer, self).__init__(*args, **kwargs)

        # Remove fields that we don't have in ES at the moment.
        self.fields.pop('upsold', None)

    def fake_object(self, data):
        """Create a fake instance of Webapp and related models from ES data."""
        is_packaged = data['app_type'] != mkt.ADDON_WEBAPP_HOSTED
        is_privileged = data['app_type'] == mkt.ADDON_WEBAPP_PRIVILEGED

        obj = Webapp(id=data['id'], app_slug=data['app_slug'],
                     is_packaged=is_packaged, icon_type='image/png')

        # Set relations and attributes we need on those relations.
        # The properties set on latest_version and current_version differ
        # because we are only setting what the serializer is going to need.
        # In particular, latest_version.is_privileged needs to be set because
        # it's used by obj.app_type_id.
        obj.listed_authors = []
        obj._current_version = Version()
        obj._current_version.addon = obj
        obj._current_version._developer_name = data['author']
        obj._current_version.supported_locales = data['supported_locales']
        obj._current_version.version = data['current_version']
        obj._latest_version = Version()
        obj._latest_version.is_privileged = is_privileged
        obj._geodata = Geodata()
        obj.all_previews = [
            Preview(id=p['id'], modified=self.to_datetime(p['modified']),
                    filetype=p['filetype'], sizes=p.get('sizes', {}))
            for p in data['previews']]
        obj.categories = data['category']
        obj.tags_list = data['tags']
        obj._device_types = [DEVICE_TYPES[d] for d in data['device']]
        obj._is_disabled = data['is_disabled']

        # Set base attributes on the "fake" app using the data from ES.
        self._attach_fields(
            obj, data, ('created', 'default_locale', 'guid', 'icon_hash',
                        'is_escalated', 'is_offline', 'last_updated',
                        'hosted_url', 'manifest_url', 'modified',
                        'premium_type', 'promo_img_hash', 'regions',
                        'reviewed', 'status'))

        # Attach translations for all translated attributes.
        self._attach_translations(
            obj, data, ('name', 'description', 'homepage',
                        'support_email', 'support_url'))
        if data.get('group_translations'):
            self._attach_translations(obj, data, ('group',))  # Feed group.
        else:
            obj.group_translations = None

        # Release notes target and source name differ (ES stores it as
        # release_notes but the db field we are emulating is called
        # releasenotes without the "_").
        ESTranslationSerializerField.attach_translations(
            obj._current_version, data, 'release_notes',
            target_name='releasenotes')

        # Set attributes that have a different name in ES.
        obj.public_stats = data['has_public_stats']

        # Override obj.get_excluded_region_ids() to just return the list of
        # regions stored in ES instead of making SQL queries.
        obj.get_excluded_region_ids = lambda: data['region_exclusions']

        # Set up payments stuff to avoid extra queries later (we'll still make
        # some, because price info is not in ES).
        if obj.is_premium():
            Webapp.attach_premiums([obj])

        # Some methods below will need the raw data from ES, put it on obj.
        obj.es_data = data

        return obj

    def create(self, data):
        return self.fake_object(data)

    def get_content_ratings(self, obj):
        body = (mkt.regions.REGION_TO_RATINGS_BODY().get(
            self._get_region_slug(), 'generic'))
        prefix = 'has_%s' % body

        # Backwards incompat with old index.
        for i, desc in enumerate(obj.es_data.get('content_descriptors', [])):
            if desc.isupper():
                obj.es_data['content_descriptors'][i] = 'has_' + desc.lower()
        for i, inter in enumerate(obj.es_data.get('interactive_elements', [])):
            if inter.isupper():
                obj.es_data['interactive_elements'][i] = 'has_' + inter.lower()

        return {
            'body': body,
            'rating': dehydrate_content_rating(
                (obj.es_data.get('content_ratings') or {})
                .get(body)) or None,
            'descriptors': [key for key in
                            obj.es_data.get('content_descriptors', [])
                            if prefix in key],
            'descriptors_text': [HUMAN_READABLE_DESCS_AND_INTERACTIVES[key]
                                 for key
                                 in obj.es_data.get('content_descriptors')
                                 if prefix in key],
            'interactives': obj.es_data.get('interactive_elements', []),
            'interactives_text': [HUMAN_READABLE_DESCS_AND_INTERACTIVES[key]
                                  for key
                                  in obj.es_data.get('interactive_elements')]
        }

    def get_feature_compatibility(self, app):
        # We're supposed to be filtering out incompatible apps anyway, so don't
        # bother calculating feature compatibility: if an app is there, it's
        # either compatible or the client overrode this by asking to see apps
        # for a different platform.
        return None

    def get_versions(self, obj):
        return dict((v['version'], v['resource_uri'])
                    for v in obj.es_data['versions'])

    def get_ratings_aggregates(self, obj):
        return obj.es_data.get('ratings', {})

    def get_upsell(self, obj):
        upsell = obj.es_data.get('upsell', False)
        if upsell:
            region_id = self.context['request'].REGION.id
            exclusions = upsell.get('region_exclusions')
            if exclusions is not None and region_id not in exclusions:
                upsell['resource_uri'] = reverse('app-detail',
                                                 kwargs={'pk': upsell['id']})
            else:
                upsell = False
        return upsell

    def get_absolute_url(self, obj):
        return absolutify(obj.get_absolute_url())

    def get_package_path(self, obj):
        return obj.es_data.get('package_path')

    def get_file_size(self, obj):
        return obj.es_data.get('file_size')

    def get_is_homescreen(self, obj):
        return obj.es_data.get('is_homescreen')


class BaseESAppFeedSerializer(ESAppSerializer):
    icons = serializers.SerializerMethodField()

    def get_icons(self, obj):
        """
        Only need the 64px icon for Feed.
        """
        return {
            '64': obj.get_icon_url(64)
        }


class ESAppFeedSerializer(BaseESAppFeedSerializer):
    """
    App serializer targetted towards the Feed, Fireplace's homepage.
    Specifically for Feed Apps/Brands that feature the whole app tile and an
    install button rather than just an icon.
    """
    class Meta(ESAppSerializer.Meta):
        fields = [
            'author', 'device_types', 'group', 'icons', 'id',
            'is_packaged', 'manifest_url', 'name', 'payment_required',
            'premium_type', 'price', 'price_locale', 'ratings', 'slug', 'user'
        ]


class ESAppFeedCollectionSerializer(BaseESAppFeedSerializer):
    """
    App serializer targetted towards the Feed, Fireplace's homepage.
    Specifically for Feed Apps, Collections, Shelves that only need app icons.
    """
    class Meta(ESAppSerializer.Meta):
        fields = [
            'device_types', 'icons', 'id', 'slug',
        ]


class SimpleAppSerializer(AppSerializer):
    """
    App serializer with fewer fields (and fewer db queries as a result).
    Used as a base for FireplaceAppSerializer and CollectionAppSerializer.
    """
    previews = SimplePreviewSerializer(many=True, required=False,
                                       source='all_previews')

    class Meta(AppSerializer.Meta):
        fields = list(
            set(AppSerializer.Meta.fields) - set(
                ['absolute_url', 'app_type', 'created', 'default_locale',
                 'package_path', 'payment_account', 'supported_locales',
                 'upsold', 'tags']))


class SimpleESAppSerializer(ESAppSerializer):
    class Meta(SimpleAppSerializer.Meta):
        pass


class SuggestionsESAppSerializer(ESAppSerializer):
    icon = serializers.SerializerMethodField()

    class Meta(ESAppSerializer.Meta):
        fields = ['name', 'description', 'absolute_url', 'icon']

    def get_icon(self, app):
        return app.get_icon_url(64)


class RocketbarESAppSerializer(serializers.Serializer):
    """Used by Firefox OS's Rocketbar apps viewer."""
    name = ESTranslationSerializerField()

    @property
    def data(self):
        if getattr(self, '_data', None) is None:
            self._data = [self.to_representation(o['payload'])
                          for o in self.instance]
        return self._data

    def to_representation(self, obj):
        # fake_app is a fake instance because we need to access a couple
        # properties and methods on Webapp. It should never hit the database.
        self.fake_app = Webapp(
            id=obj['id'], icon_type='image/png',
            default_locale=obj.get('default_locale', settings.LANGUAGE_CODE),
            icon_hash=obj.get('icon_hash'),
            modified=es_to_datetime(obj['modified']))
        ESTranslationSerializerField.attach_translations(
            self.fake_app, obj, 'name')
        return {
            'name': self.fields['name'].to_representation(
                self.fields['name'].get_attribute(self.fake_app)),
            'icon': self.fake_app.get_icon_url(64),
            'slug': obj['slug'],
            'manifest_url': obj['manifest_url'],
        }


class RocketbarESAppSerializerV2(AppSerializer, RocketbarESAppSerializer):
    """
    Replaced `icon` key with `icons` for various pixel sizes: 128, 64, 48, 32.
    """

    def to_representation(self, obj):
        data = super(RocketbarESAppSerializerV2, self).to_representation(obj)
        del data['icon']
        data['icons'] = self.get_icons(self.fake_app)
        return data
