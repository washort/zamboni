# -*- coding: utf-8 -*-
import json
from decimal import Decimal

from django.core.urlresolvers import reverse

import mock
from elasticutils.contrib.django import S
from nose.tools import eq_, ok_
from test_utils import RequestFactory

import amo
import amo.tests
from addons.models import AddonCategory, AddonDeviceType, Category, Preview
from market.models import PriceCurrency

import mkt
from mkt.constants import ratingsbodies, regions
from mkt.developers.models import (AddonPaymentAccount, PaymentAccount,
                                   SolitudeSeller)
from mkt.search.serializers import ESAppSerializer
from mkt.site.fixtures import fixture
from mkt.webapps.api import AppSerializer
from mkt.webapps.models import Installed, Webapp, WebappIndexer
from mkt.webapps.utils import (dehydrate_content_rating,
                               _filter_iarc_obj_by_region,
                               get_supported_locales)
from users.models import UserProfile
from versions.models import Version


class TestAppSerializer(amo.tests.TestCase):
    fixtures = fixture('user_2519')

    def setUp(self):
        self.app = amo.tests.app_factory(version_kw={'version': '1.8'})
        self.profile = UserProfile.objects.get(pk=2519)
        self.request = RequestFactory().get('/')

    def serialize(self, app, profile=None):
        self.request.amo_user = profile
        a = AppSerializer(instance=app, context={'request': self.request})
        return a.data

    def test_no_previews(self):
        eq_(self.serialize(self.app)['previews'], [])

    def test_with_preview(self):
        obj = Preview.objects.create(**{
            'filetype': 'image/png', 'thumbtype': 'image/png',
            'addon': self.app})
        preview = self.serialize(self.app)['previews'][0]
        self.assertSetEqual(preview, ['filetype', 'id', 'image_url',
                                      'thumbnail_url', 'resource_uri'])
        eq_(int(preview['id']), obj.pk)

    def test_no_rating(self):
        eq_(self.serialize(self.app)['content_ratings']['ratings'], None)

    def test_no_price(self):
        res = self.serialize(self.app)
        eq_(res['price'], None)
        eq_(res['price_locale'], None)
        eq_(res['payment_required'], False)

    def check_profile(self, profile, **kw):
        expected = {'developed': False, 'installed': False, 'purchased': False}
        expected.update(**kw)
        eq_(profile, expected)

    def test_installed(self):
        self.app.installed.create(user=self.profile)
        res = self.serialize(self.app, profile=self.profile)
        self.check_profile(res['user'], installed=True)

    def test_purchased(self):
        self.app.addonpurchase_set.create(user=self.profile)
        res = self.serialize(self.app, profile=self.profile)
        self.check_profile(res['user'], purchased=True)

    def test_owned(self):
        self.app.addonuser_set.create(user=self.profile)
        res = self.serialize(self.app, profile=self.profile)
        self.check_profile(res['user'], developed=True)

    def test_locales(self):
        res = self.serialize(self.app)
        eq_(res['default_locale'], 'en-US')
        eq_(res['supported_locales'], [])

    def test_multiple_locales(self):
        self.app.current_version.update(supported_locales='en-US,it')
        res = self.serialize(self.app)
        self.assertSetEqual(res['supported_locales'], ['en-US', 'it'])

    def test_regions(self):
        res = self.serialize(self.app)
        self.assertSetEqual([region['slug'] for region in res['regions']],
                            [region.slug for region in self.app.get_regions()])

    def test_current_version(self):
        res = self.serialize(self.app)
        ok_('current_version' in res)
        eq_(res['current_version'], self.app.current_version.version)

    def test_versions_one(self):
        res = self.serialize(self.app)
        self.assertSetEqual([v.version for v in self.app.versions.all()],
                            res['versions'].keys())

    def test_versions_multiple(self):
        ver = Version.objects.create(addon=self.app, version='1.9')
        self.app.update(_current_version=ver, _latest_version=ver)
        res = self.serialize(self.app)
        eq_(res['current_version'], ver.version)
        self.assertSetEqual([v.version for v in self.app.versions.all()],
                            res['versions'].keys())

    def test_categories(self):
        cat1 = Category.objects.create(type=amo.ADDON_WEBAPP, slug='cat1')
        cat2 = Category.objects.create(type=amo.ADDON_WEBAPP, slug='cat2')
        AddonCategory.objects.create(addon=self.app, category=cat1)
        AddonCategory.objects.create(addon=self.app, category=cat2)
        res = self.serialize(self.app)
        self.assertSetEqual(res['categories'], ['cat1', 'cat2'])

    def test_content_ratings(self):
        self.create_switch('iarc', db=True)
        self.app.set_content_ratings({
            ratingsbodies.CLASSIND: ratingsbodies.CLASSIND_18,
            ratingsbodies.GENERIC: ratingsbodies.GENERIC_18,
        })
        res = self.serialize(self.app)
        eq_(res['content_ratings']['ratings']['classind'],
            {'body': 'CLASSIND',
             'body_label': 'classind',
             'rating': 'For ages 18+',
             'rating_label': '18',
             'description': unicode(ratingsbodies.DESC_LAZY) % 18})
        eq_(res['content_ratings']['ratings']['generic'],
            {'body': 'Generic',
             'body_label': 'generic',
             'rating': 'For ages 18+',
             'rating_label': '18',
             'description': unicode(ratingsbodies.DESC_LAZY) % 18})

    def test_content_ratings_by_region(self):
        self.create_switch('iarc', db=True)
        self.app.set_content_ratings({
            ratingsbodies.CLASSIND: ratingsbodies.CLASSIND_18,
            ratingsbodies.GENERIC: ratingsbodies.GENERIC_18,
        })
        self.app.set_descriptors(['has_classind_lang', 'has_generic_lang'])

        self.request.REGION = mkt.regions.BR
        res = self.serialize(self.app)['content_ratings']

        for iarc_obj in ('ratings', 'descriptors', 'regions'):
            eq_(len(res[iarc_obj]), 1)
        for iarc_obj in ('ratings', 'descriptors'):
            assert 'classind' in res[iarc_obj], iarc_obj
        assert 'br' in res['regions']

    def test_content_ratings_regions(self):
        self.create_switch('iarc', db=True)
        res = self.serialize(self.app)
        region_rating_bodies = res['content_ratings']['regions']
        eq_(region_rating_bodies['br'], 'classind')
        eq_(region_rating_bodies['de'], 'usk')
        eq_(region_rating_bodies['es'], 'pegi')
        eq_(region_rating_bodies['us'], 'esrb')

    def test_content_descriptors(self):
        self.app.set_descriptors(['has_esrb_blood', 'has_pegi_scary'])
        res = self.serialize(self.app)
        eq_(dict(res['content_ratings']['descriptors']),
            {'esrb': [{'label': 'blood', 'name': 'Blood'}],
             'pegi': [{'label': 'scary', 'name': 'Fear'}]})

    def test_interactive_elements(self):
        self.app.set_interactives(['has_digital_purchases', 'has_shares_info'])
        res = self.serialize(self.app)
        eq_(res['content_ratings']['interactive_elements'],
            [{'label': 'shares-info', 'name': 'Shares Info'},
             {'label': 'digital-purchases', 'name': 'Digital Purchases'}])

    def test_dehydrate_content_rating_old_es(self):
        """Test dehydrate works with old ES mapping."""
        self.create_switch('iarc', db=True)

        rating = dehydrate_content_rating(
            [json.dumps({'body': u'CLASSIND',
                         'slug': u'0',
                         'description': u'General Audiences',
                         'name': u'0+',
                         'body_slug': u'classind'})])
        eq_(rating, {})

    def test_filter_iarc_obj_by_region_only(self):
        self.create_switch('iarc', db=True)

        region_map = {
            'us': 'esrb',
            'mx': 'esrb',
            'es': 'pegi',
            'br': 'classind',
        }

        for region in region_map:
            eq_(_filter_iarc_obj_by_region(region_map, region=region),
                {region: region_map[region]})
        eq_(_filter_iarc_obj_by_region(region_map, region='DNE'), region_map)

    def test_filter_iarc_obj_by_region_and_body(self):
        self.create_switch('iarc', db=True)

        classind_rating = {
            'body': u'CLASSIND',
            'slug': u'0',
            'description': u'General Audiences',
            'name': u'0+',
            'body_slug': u'classind'
        }
        esrb_rating = {
            'body': u'ESRB',
            'slug': u'18',
            'description': u'Adults Only 18+',
            'name': u'18+',
            'body_slug': u'esrb'
        }
        content_ratings = {
            'classind': classind_rating,
            'esrb': esrb_rating
        }

        esrb_only = _filter_iarc_obj_by_region(
            content_ratings, region='us', lookup_body=True)
        eq_(esrb_only, {'esrb': esrb_rating})

        classind_only = _filter_iarc_obj_by_region(
            content_ratings, region='br', lookup_body=True)
        eq_(classind_only, {'classind': classind_rating})

        no_rating_for_region = _filter_iarc_obj_by_region(
            content_ratings, region='es', lookup_body=True)
        eq_(no_rating_for_region, content_ratings)

    def test_no_release_notes(self):
        res = self.serialize(self.app)
        eq_(res['release_notes'], None)

        self.app.current_version.delete()
        self.app.update_version()
        eq_(self.app.current_version, None)
        res = self.serialize(self.app)
        eq_(res['release_notes'], None)

    def test_release_notes(self):
        version = self.app.current_version
        version.releasenotes = u'These are nötes.'
        version.save()
        res = self.serialize(self.app)
        eq_(res['release_notes'], {u'en-US': unicode(version.releasenotes)})

        self.request = RequestFactory().get('/?lang=whatever')
        res = self.serialize(self.app)
        eq_(res['release_notes'], unicode(version.releasenotes))

    def test_upsell(self):
        self.request.REGION = mkt.regions.US
        upsell = amo.tests.app_factory()
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)

        res = self.serialize(self.app)
        eq_(res['upsell']['id'], upsell.id)
        eq_(res['upsell']['app_slug'], upsell.app_slug)
        eq_(res['upsell']['name'], upsell.name)
        eq_(res['upsell']['icon_url'], upsell.get_icon_url(128))
        self.assertApiUrlEqual(res['upsell']['resource_uri'],
                               '/apps/app/%s/' % upsell.id)

    def test_upsell_not_public(self):
        self.request.REGION = mkt.regions.US
        upsell = amo.tests.app_factory(disabled_by_user=True)
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)

        res = self.serialize(self.app)
        eq_(res['upsell'], False)

    def test_upsell_excluded_from_region(self):
        self.request.REGION = mkt.regions.US
        upsell = amo.tests.app_factory()
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)
        upsell.addonexcludedregion.create(region=mkt.regions.US.id)

        res = self.serialize(self.app)
        eq_(res['upsell'], False)

    def test_upsell_region_without_payments(self):
        upsell = amo.tests.app_factory()
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)

        region_id = list(upsell.get_excluded_region_ids())[0]
        self.request.REGION = mkt.regions.REGIONS_CHOICES_ID[region_id][1]

        res = self.serialize(self.app)
        eq_(res['upsell'], False)


class TestAppSerializerPrices(amo.tests.TestCase):
    fixtures = fixture('user_2519')

    def setUp(self):
        self.app = amo.tests.app_factory(premium_type=amo.ADDON_PREMIUM)
        self.profile = UserProfile.objects.get(pk=2519)
        self.create_flag('override-app-purchase', everyone=True)
        self.request = RequestFactory().get('/')

    def serialize(self, app, profile=None, region=None, request=None):
        if request is None:
            request = self.request
        request.amo_user = self.profile
        request.REGION = region
        a = AppSerializer(instance=app, context={'request': request})
        return a.data

    def test_some_price(self):
        self.make_premium(self.app, price='0.99')
        res = self.serialize(self.app, region=regions.US)
        eq_(res['price'], Decimal('0.99'))
        eq_(res['price_locale'], '$0.99')
        eq_(res['payment_required'], True)

    def test_no_charge(self):
        self.make_premium(self.app, price='0.00')
        res = self.serialize(self.app, region=regions.US)
        eq_(res['price'], Decimal('0.00'))
        eq_(res['price_locale'], '$0.00')
        eq_(res['payment_required'], False)

    def test_wrong_region(self):
        self.make_premium(self.app, price='0.99')
        res = self.serialize(self.app, region=regions.PL)
        eq_(res['price'], None)
        eq_(res['price_locale'], None)
        eq_(res['payment_required'], True)

    def test_with_locale(self):
        premium = self.make_premium(self.app, price='0.99')
        PriceCurrency.objects.create(region=regions.PL.id, currency='PLN',
                                     price='5.01', tier=premium.price,
                                     provider=1)

        with self.activate(locale='fr'):
            res = self.serialize(self.app, region=regions.PL)
            eq_(res['price'], Decimal('5.01'))
            eq_(res['price_locale'], u'5,01\xa0PLN')

    def test_missing_price(self):
        premium = self.make_premium(self.app, price='0.99')
        premium.price = None
        premium.save()

        res = self.serialize(self.app)
        eq_(res['price'], None)
        eq_(res['price_locale'], None)

    def test_cannot_purchase(self):
        self.make_premium(self.app, price='0.99')
        res = self.serialize(self.app, region=regions.UK)
        eq_(res['price'], None)
        eq_(res['price_locale'], None)
        eq_(res['payment_required'], True)

    def test_can_purchase(self):
        self.make_premium(self.app, price='0.99')
        res = self.serialize(self.app, region=regions.UK)
        eq_(res['price'], None)
        eq_(res['price_locale'], None)
        eq_(res['payment_required'], True)


@mock.patch('versions.models.Version.is_privileged', False)
class TestESAppToDict(amo.tests.ESTestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        self.profile = UserProfile.objects.get(pk=2519)
        self.request = RequestFactory().get('/')
        self.request.REGION = mkt.regions.US
        self.request.amo_user = self.profile
        self.app = Webapp.objects.get(pk=337141)
        self.version = self.app.current_version
        self.category = Category.objects.create(name='cattest', slug='testcat',
                                                 type=amo.ADDON_WEBAPP)
        AddonCategory.objects.create(addon=self.app, category=self.category)
        self.preview = Preview.objects.create(filetype='image/png',
                                              addon=self.app, position=0)
        self.app.description = {
            'en-US': u'XSS attempt <script>alert(1)</script>',
            'fr': u'Déscriptîon in frènch'
        }
        self.app.save()
        self.refresh('webapp')

    def get_obj(self):
        return S(WebappIndexer).filter(id=self.app.pk).execute().objects[0]

    def serialize(self):
        serializer = ESAppSerializer(instance=self.get_obj(),
                                     context={'request': self.request})
        return serializer.data

    def test_basic(self):
        res = self.serialize()
        expected = {
            'absolute_url': 'http://testserver/app/something-something/',
            'app_type': 'hosted',
            'author': 'Mozilla Tester',
            'banner_regions': [],
            'categories': [self.category.slug],
            'created': self.app.created,
            'current_version': '1.0',
            'default_locale': u'en-US',
            'description': {
                'en-US': u'XSS attempt &lt;script&gt;alert(1)&lt;/script&gt;',
                'fr': u'Déscriptîon in frènch'
            },
            'device_types': [],
            'homepage': None,
            'icons': dict((size, self.app.get_icon_url(size))
                          for size in (16, 48, 64, 128)),
            'id': 337141,
            'is_offline': False,
            'is_packaged': False,
            'manifest_url': 'http://micropipes.com/temp/steamcube.webapp',
            'name': {u'en-US': u'Something Something Steamcube!',
                     u'es': u'Algo Algo Steamcube!'},
            'payment_required': False,
            'premium_type': 'free',
            'previews': [{'thumbnail_url': self.preview.thumbnail_url,
                          'image_url': self.preview.image_url}],
            'privacy_policy': reverse('app-privacy-policy-detail',
                                      kwargs={'pk': self.app.id}),
            'public_stats': False,
            'ratings': {
                'average': 0.0,
                'count': 0,
            },
            'reviewed': self.version.reviewed,
            'slug': 'something-something',
            'status': 4,
            'support_email': None,
            'support_url': None,
            'supported_locales': [u'en-US', u'es', u'pt-BR'],
            'upsell': False,
            # 'version's handled below to support API URL assertions.
            'weekly_downloads': None,
        }

        if self.request.amo_user:
            expected['user'] = {
                'developed': False,
                'installed': False,
                'purchased': False,
            }

        ok_('1.0' in res['versions'])
        self.assertApiUrlEqual(res['versions']['1.0'],
                               '/apps/versions/1268829/')

        for k, v in expected.items():
            eq_(res[k], v,
                u'Expected value "%s" for field "%s", got "%s"' %
                (v, k, res[k]))

    def test_regions(self):
        res = self.serialize()
        self.assertSetEqual([region['slug'] for region in res['regions']],
                            [region.slug for region in self.app.get_regions()])

    def test_basic_no_queries(self):
        # If we don't pass a UserProfile, a free app shouldn't have to make any
        # db queries at all. To prevent a potential query because of iarc check,
        # we create the iarc waffle switch, it should be cached immediately.
        self.create_switch('iarc')
        self.request.amo_user = None
        with self.assertNumQueries(0):
            self.test_basic()

    def test_basic_with_lang(self):
        # Check that when ?lang is passed, we get the right language and we get
        # empty strings instead of None if the strings don't exist.
        self.request = RequestFactory().get('/?lang=es')
        self.request.REGION = mkt.regions.US
        res = self.serialize()
        expected = {
            'id': 337141,
            'description': u'XSS attempt &lt;script&gt;alert(1)&lt;/script&gt;',
            'homepage': None,
            'name': u'Algo Algo Steamcube!',
            'support_email': None,
            'support_url': None,
        }

        for k, v in expected.items():
            eq_(res[k], v,
                u'Expected value "%s" for field "%s", got "%s"' %
                (v, k, res[k]))

    def test_content_ratings(self):
        self.request.REGION = mkt.regions.RESTOFWORLD
        self.create_switch('iarc')
        self.app.set_content_ratings({
            ratingsbodies.CLASSIND: ratingsbodies.CLASSIND_18,
            ratingsbodies.GENERIC: ratingsbodies.GENERIC_18,
        })
        self.app.set_descriptors(['has_esrb_blood', 'has_pegi_scary'])
        self.app.set_interactives(['has_digital_purchases', 'has_shares_info'])
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['content_ratings']['ratings']['classind'],
            {'body': 'CLASSIND',
             'body_label': 'classind',
             'rating': 'For ages 18+',
             'rating_label': '18',
             'description': unicode(ratingsbodies.DESC_LAZY) % 18})
        eq_(res['content_ratings']['ratings']['generic'],
            {'body': 'Generic',
             'body_label': 'generic',
             'rating': 'For ages 18+',
             'rating_label': '18',
             'description': unicode(ratingsbodies.DESC_LAZY) % 18})

        eq_(dict(res['content_ratings']['descriptors']),
            {'esrb': [{'label': 'blood', 'name': 'Blood'}],
             'pegi': [{'label': 'scary', 'name': 'Fear'}]})
        eq_(sorted(res['content_ratings']['interactive_elements'],
                   key=lambda x: x['name']),
            [{'label': 'digital-purchases', 'name': 'Digital Purchases'},
             {'label': 'shares-info', 'name': 'Shares Info'}])

    def test_content_ratings_by_region(self):
        self.create_switch('iarc')
        self.app.set_content_ratings({
            ratingsbodies.CLASSIND: ratingsbodies.CLASSIND_18,
            ratingsbodies.GENERIC: ratingsbodies.GENERIC_18,
        })
        self.app.set_descriptors(['has_classind_lang', 'has_generic_lang'])
        self.app.save()
        self.refresh('webapp')

        self.request.REGION = mkt.regions.BR
        res = self.serialize()['content_ratings']

        for iarc_obj in ('ratings', 'descriptors', 'regions'):
            eq_(len(res[iarc_obj]), 1)
        for iarc_obj in ('ratings', 'descriptors'):
            assert 'classind' in res[iarc_obj], iarc_obj
        assert 'br' in res['regions']

    def test_content_ratings_regions(self):
        self.request.REGION = mkt.regions.RESTOFWORLD
        self.create_switch('iarc')
        res = self.serialize()
        region_rating_bodies = res['content_ratings']['regions']
        eq_(region_rating_bodies['br'], 'classind')
        eq_(region_rating_bodies['de'], 'usk')
        eq_(region_rating_bodies['es'], 'pegi')
        eq_(region_rating_bodies['us'], 'esrb')

    def test_content_ratings_regions_no_switch(self):
        self.app.set_content_ratings({
            ratingsbodies.CLASSIND: ratingsbodies.CLASSIND_18,
            ratingsbodies.GENERIC: ratingsbodies.GENERIC_18,
        })
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        assert 'us' not in res['content_ratings']['regions']
        eq_(res['content_ratings']['regions']['br'], 'classind')

    def test_show_downloads_count(self):
        """Show weekly_downloads in results if app stats are public."""
        self.app.update(public_stats=True)
        self.refresh('webapp')
        res = self.serialize()
        eq_(res['weekly_downloads'], 9999)

    def test_devices(self):
        AddonDeviceType.objects.create(addon=self.app,
                                       device_type=amo.DEVICE_GAIA.id)
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['device_types'], ['firefoxos'])

    def test_user(self):
        self.app.addonuser_set.create(user=self.profile)
        self.profile.installed_set.create(addon=self.app)
        self.app.addonpurchase_set.create(user=self.profile)
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['user'],
            {'developed': True, 'installed': True, 'purchased': True})

    def test_user_not_mine(self):
        self.app.addonuser_set.create(user_id=31337)
        Installed.objects.create(addon=self.app, user_id=31337)
        self.app.addonpurchase_set.create(user_id=31337)
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['user'],
            {'developed': False, 'installed': False, 'purchased': False})

    def test_no_price(self):
        res = self.serialize()
        eq_(res['price'], None)
        eq_(res['price_locale'], None)

    def test_has_price(self):
        self.make_premium(self.app)
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['price'], Decimal('1.00'))
        eq_(res['price_locale'], '$1.00')
        eq_(res['payment_required'], True)

    def test_not_paid(self):
        self.make_premium(self.app)
        PriceCurrency.objects.update(paid=False)
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['price'], None)
        eq_(res['price_locale'], None)

    def test_no_currency(self):
        self.make_premium(self.app)
        PriceCurrency.objects.all().delete()
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['price'], None)
        eq_(res['price_locale'], None)

    def test_payment_account(self):
        self.make_premium(self.app)
        seller = SolitudeSeller.objects.create(
            resource_uri='/path/to/sel', uuid='seller-id', user=self.profile)
        account = PaymentAccount.objects.create(
            user=self.profile, uri='asdf', name='test', inactive=False,
            solitude_seller=seller, account_id=123)
        addon_payment_account = AddonPaymentAccount.objects.create(
            addon=self.app, account_uri='foo', payment_account=account,
            product_uri='bpruri')
        self.app.save()
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['payment_account'], reverse('payment-account-detail',
            kwargs={'pk': addon_payment_account.pk}))

    def test_release_notes(self):
        res = self.serialize()
        eq_(res['release_notes'], None)
        version = self.app.current_version
        version.releasenotes = u'These are nötes.'
        version.save()
        self.app.save()
        self.refresh('webapp')
        res = self.serialize()
        eq_(res['release_notes'], {u'en-US': unicode(version.releasenotes)})

        self.request = RequestFactory().get('/?lang=whatever')
        self.request.REGION = mkt.regions.US
        res = self.serialize()
        eq_(res['release_notes'], unicode(version.releasenotes))

    def test_upsell(self):
        upsell = amo.tests.app_factory()
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['upsell']['id'], upsell.id)
        eq_(res['upsell']['app_slug'], upsell.app_slug)
        eq_(res['upsell']['name'], upsell.name)
        eq_(res['upsell']['icon_url'], upsell.get_icon_url(128))
        self.assertApiUrlEqual(res['upsell']['resource_uri'],
                               '/apps/app/%s/' % upsell.id)

    def test_upsell_not_public(self):
        upsell = amo.tests.app_factory(disabled_by_user=True)
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['upsell'], False)

    def test_upsell_is_made_public_later(self):
        upsell = amo.tests.app_factory(status=amo.STATUS_PENDING)
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)

        # Don't use .reload() because it doesn't reset cached_property.
        upsell = Webapp.objects.get(pk=upsell.pk)
        upsell.update(status=amo.STATUS_PUBLIC)

        # Note that we shouldn't have to call self.app.save(), because saving
        # the upsell should have triggered the reindex of self.app.
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['upsell']['id'], upsell.id)
        eq_(res['upsell']['app_slug'], upsell.app_slug)
        eq_(res['upsell']['name'], upsell.name)
        eq_(res['upsell']['icon_url'], upsell.get_icon_url(128))
        self.assertApiUrlEqual(res['upsell']['resource_uri'],
                               '/apps/app/%s/' % upsell.id)

    def test_upsell_excluded_from_region(self):
        upsell = amo.tests.app_factory()
        upsell.addonexcludedregion.create(region=mkt.regions.US.id)
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)
        self.refresh('webapp')

        res = self.serialize()
        eq_(res['upsell'], False)

    def test_upsell_region_without_payments(self):
        upsell = amo.tests.app_factory()
        self.make_premium(upsell)
        self.app._upsell_from.create(premium=upsell)
        self.refresh('webapp')

        region_id = list(upsell.get_excluded_region_ids())[0]
        region = mkt.regions.REGIONS_CHOICES_ID[region_id][1]
        self.request.REGION = region

        res = self.serialize()
        eq_(res['upsell'], False)

    def test_developer_name_empty(self):
        self.app.current_version.update(_developer_name='')
        self.app.addonuser_set.update(listed=False)
        self.app.save()
        self.refresh('webapp')
        res = self.serialize()
        eq_(res['author'], '')


class TestSupportedLocales(amo.tests.TestCase):

    def setUp(self):
        self.manifest = {'default_locale': 'en'}

    def check(self, expected):
        eq_(get_supported_locales(self.manifest), expected)

    def test_empty_locale(self):
        self.check([])

    def test_single_locale(self):
        self.manifest.update({'locales': {'es': {'name': 'eso'}}})
        self.check(['es'])

    def test_multiple_locales(self):
        self.manifest.update({'locales': {'es': {'name': 'si'},
                                          'fr': {'name': 'oui'}}})
        self.check(['es', 'fr'])

    def test_short_locale(self):
        self.manifest.update({'locales': {'pt': {'name': 'sim'}}})
        self.check(['pt-PT'])

    def test_unsupported_locale(self):
        self.manifest.update({'locales': {'xx': {'name': 'xx'}}})
        self.check([])
