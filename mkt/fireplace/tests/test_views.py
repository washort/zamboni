# -*- coding: utf-8 -*-
import json
from urlparse import urlparse

from django.core.urlresolvers import reverse
from django.test.client import RequestFactory

from mock import patch
from nose.tools import eq_, ok_

import mkt
from mkt.api.tests import BaseAPI
from mkt.api.tests.test_oauth import RestOAuth
from mkt.fireplace.serializers import (FireplaceAppSerializer,
                                       FireplaceWebsiteSerializer)
from mkt.search.forms import COLOMBIA_WEBSITE
from mkt.site.fixtures import fixture
from mkt.site.tests import app_factory, ESTestCase, TestCase
from mkt.tags.models import Tag
from mkt.webapps.models import WebappUser, Installed, Webapp
from mkt.websites.models import Website, WebsitePopularity
from mkt.websites.utils import website_factory


# https://bugzilla.mozilla.org/show_bug.cgi?id=958608#c1 and #c2.
FIREPLACE_APP_EXCLUDED_FIELDS = (
    'absolute_url', 'app_type', 'created', 'default_locale', 'payment_account',
    'regions', 'resource_uri', 'supported_locales', 'upsold', 'versions')

FIREPLACE_WEBSITE_EXCLUDED_FIELDS = ('title', )


def assert_fireplace_app(data):
    for field in FIREPLACE_APP_EXCLUDED_FIELDS:
        ok_(field not in data, field)
    for field in FireplaceAppSerializer.Meta.fields:
        ok_(field in data, field)


def assert_fireplace_website(data):
    for field in FIREPLACE_WEBSITE_EXCLUDED_FIELDS:
        ok_(field not in data, field)
    for field in FireplaceWebsiteSerializer.Meta.fields:
        ok_(field in data, field)


class TestAppDetail(BaseAPI):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        super(TestAppDetail, self).setUp()
        self.url = reverse('fireplace-app-detail', kwargs={'pk': 337141})

    def test_get(self):
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['id'], 337141)
        assert_fireplace_app(data)

    def test_get_slug(self):
        Webapp.objects.get(pk=337141).update(app_slug='foo')
        res = self.client.get(reverse('fireplace-app-detail',
                                      kwargs={'pk': 'foo'}))
        data = json.loads(res.content)
        eq_(data['id'], 337141)

    def test_others(self):
        url = reverse('fireplace-app-list')
        self._allowed_verbs(self.url, ['get'])
        self._allowed_verbs(url, [])

    def test_file_size(self):
        self.app = Webapp.objects.get(pk=337141)

        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['file_size'], u'379.0\xa0KB')

        file_ = self.app.current_version.all_files[0]
        file_.update(size=1024 * 1024 * 1.1)
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['file_size'], u'1.1\xa0MB')

        file_.update(size=0)
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['file_size'], None)


class TestFeaturedSearchView(RestOAuth, ESTestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestFeaturedSearchView, self).setUp()
        self.webapp = Webapp.objects.get(pk=337141)
        self.reindex(Webapp)
        self.url = reverse('fireplace-featured-search-api')

    def test_get(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        objects = res.json['objects']
        eq_(len(objects), 1)
        data = objects[0]
        eq_(data['id'], 337141)
        assert_fireplace_app(data)

        # fireplace-featured-search-api is only kept for yogafire, which does
        # not care about collection data, so we don't even need to add empty
        # arrays for backwards-compatibility.
        ok_('collections' not in res.json)
        ok_('featured' not in res.json)
        ok_('operator' not in res.json)


class TestSearchView(RestOAuth, ESTestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestSearchView, self).setUp()
        self.webapp = Webapp.objects.get(pk=337141)
        self.reindex(Webapp)
        self.url = reverse('fireplace-search-api')

    def test_get(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        objects = res.json['objects']
        eq_(len(objects), 1)
        data = objects[0]
        eq_(data['id'], 337141)
        assert_fireplace_app(data)
        ok_('featured' not in res.json)
        ok_('collections' not in res.json)
        ok_('operator' not in res.json)

    def test_anonymous_user(self):
        res = self.anon.get(self.url)
        eq_(res.status_code, 200)
        data = res.json['objects'][0]
        eq_(data['user'], None)

        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        data = res.json['objects'][0]
        eq_(data['user'], None)

    def test_icons(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        objects = res.json['objects']
        data = objects[0]['icons']
        eq_(len(data), 2)
        eq_(urlparse(data['64'])[0:3],
            urlparse(self.webapp.get_icon_url(64))[0:3])
        eq_(urlparse(data['128'])[0:3],
            urlparse(self.webapp.get_icon_url(128))[0:3])


class TestMultiSearchView(RestOAuth, ESTestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestMultiSearchView, self).setUp()
        self.url = reverse('fireplace-multi-search-api')
        self.website = website_factory()
        self.website.popularity.add(WebsitePopularity(region=0, value=666))
        self.webapp = Webapp.objects.get(pk=337141)
        self.reindex(Webapp)
        self.reindex(Website)

    def tearDown(self):
        Website.get_indexer().unindexer(_all=True)
        super(TestMultiSearchView, self).tearDown()

    def _add_co_tag(self, website):
        co = Tag.objects.get_or_create(tag_text=COLOMBIA_WEBSITE)[0]
        website.keywords.add(co)
        self.reindex(Website)

    def test_get_multi(self):
        res = self.client.get(self.url)
        objects = res.json['objects']
        eq_(objects[0]['doc_type'], 'website')
        assert_fireplace_website(objects[0])
        eq_(objects[0]['slug'], '{website-%d}' % self.website.pk)
        eq_(objects[1]['doc_type'], 'webapp')
        assert_fireplace_app(objects[1])

    def test_get_multi_colombia(self):
        self._add_co_tag(self.website)
        res = self.client.get(self.url, {'doc_type': 'website',
                                         'region': 'mx'})
        eq_(res.json['meta']['total_count'], 0)
        res_co = self.client.get(self.url, {'doc_type': 'website',
                                            'region': 'co'})
        eq_(res_co.json['meta']['total_count'], 1)
        ok_(COLOMBIA_WEBSITE in res_co.json['objects'][0]['keywords'])
        assert_fireplace_website(res_co.json['objects'][0])

    def test_icons(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        objects = res.json['objects']
        eq_(objects[0]['doc_type'], 'website')
        data = objects[0]['icons']
        eq_(len(data), 2)
        eq_(urlparse(data['64'])[0:3],
            urlparse(self.website.get_icon_url(64))[0:3])
        eq_(urlparse(data['128'])[0:3],
            urlparse(self.website.get_icon_url(128))[0:3])
        eq_(objects[1]['doc_type'], 'webapp')
        data = objects[1]['icons']
        eq_(len(data), 2)
        eq_(urlparse(data['64'])[0:3],
            urlparse(self.webapp.get_icon_url(64))[0:3])
        eq_(urlparse(data['128'])[0:3],
            urlparse(self.webapp.get_icon_url(128))[0:3])


class TestConsumerInfoView(RestOAuth, TestCase):
    fixtures = fixture('user_2519')

    def setUp(self):
        super(TestConsumerInfoView, self).setUp()
        self.request = RequestFactory().get('/')
        self.url = reverse('fireplace-consumer-info')

    @patch('mkt.regions.middleware.GeoIP.lookup')
    def test_geoip_called_api_v1(self, mock_lookup):
        # When we increment settings.API_CURRENT_VERSION, we'll need to update
        # this test to make sure it's still only using v1.
        self.url = reverse('fireplace-consumer-info')
        ok_('/api/v1/' in self.url)
        mock_lookup.return_value = mkt.regions.GBR
        res = self.anon.get(self.url)
        data = json.loads(res.content)
        eq_(data['region'], 'uk')
        eq_(mock_lookup.call_count, 1)

    @patch('mkt.regions.middleware.GeoIP.lookup')
    def test_geoip_called_api_v2(self, mock_lookup):
        self.url = reverse('api-v2:fireplace-consumer-info')
        mock_lookup.return_value = mkt.regions.GBR
        res = self.anon.get(self.url)
        data = json.loads(res.content)
        eq_(data['region'], 'uk')
        eq_(mock_lookup.call_count, 1)

    @patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_no_user_just_region(self, region_from_request):
        region_from_request.return_value = mkt.regions.GBR
        res = self.anon.get(self.url)
        data = json.loads(res.content)
        eq_(len(data.keys()), 1)
        eq_(data['region'], 'uk')

    @patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_recommendation_opt_out(self, region_from_request):
        region_from_request.return_value = mkt.regions.BRA
        for opt in (True, False):
            self.user.update(enable_recommendations=opt)
            res = self.client.get(self.url)
            data = json.loads(res.content)
            eq_(data['enable_recommendations'], opt)

    @patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_with_user_developed(self, region_from_request):
        region_from_request.return_value = mkt.regions.BRA
        developed_app = app_factory()
        WebappUser.objects.create(user=self.user, webapp=developed_app)
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['region'], 'br')
        eq_(data['apps']['installed'], [])
        eq_(data['apps']['developed'], [developed_app.pk])
        eq_(data['apps']['purchased'], [])

    @patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_with_user_installed(self, region_from_request):
        region_from_request.return_value = mkt.regions.BRA
        installed_app = app_factory()
        Installed.objects.create(user=self.user, webapp=installed_app)
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['region'], 'br')
        eq_(data['apps']['installed'], [installed_app.pk])
        eq_(data['apps']['developed'], [])
        eq_(data['apps']['purchased'], [])

    @patch('mkt.users.models.UserProfile.purchase_ids')
    @patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_with_user_purchased(self, region_from_request, purchase_ids):
        region_from_request.return_value = mkt.regions.BRA
        purchased_app = app_factory()
        purchase_ids.return_value = [purchased_app.pk]
        res = self.client.get(self.url)
        data = json.loads(res.content)
        eq_(data['region'], 'br')
        eq_(data['apps']['installed'], [])
        eq_(data['apps']['developed'], [])
        eq_(data['apps']['purchased'], [purchased_app.pk])


class TestRocketFuelRedirect(TestCase):
    def setUp(self):
        super(TestRocketFuelRedirect, self).setUp()
        self.url = '/api/v1/fireplace/collection/tarako-featured/'
        self.target_url = '/api/v2/fireplace/feed/collections/tarako-featured/'

    def test_redirect(self):
        response = self.client.get(self.url)
        self.assertCORS(response, 'GET')
        self.assert3xx(response, self.target_url,
                       status_code=301)

    def test_redirect_with_query_params(self):
        self.url += u'?foo=bar&re=diré'
        self.target_url += '?foo=bar&re=dir%C3%A9'
        self.test_redirect()
