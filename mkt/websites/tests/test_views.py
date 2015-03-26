import json

from django.core.urlresolvers import reverse
from django.contrib.auth.models import AnonymousUser
from django.test.client import RequestFactory

import mock
from nose.tools import eq_

from mkt.site.tests import ESTestCase, TestCase

from mkt.websites.models import Website, MemoryStore, MemoryWebsite
from mkt.websites.utils import website_factory
from mkt.websites.views import WebsiteSearchView


class TestWebsiteESView(ESTestCase):
    def setUp(self):
        self.website = website_factory()
        super(TestWebsiteESView, self).setUp()
        self._reindex()

    def _reindex(self):
        self.reindex(Website, 'mkt_website')

    def _test_get(self):
        # The view is not registered in urls.py at the moment, so we call it
        # and render the response manually instead of letting django do it for
        # us.
        self.req = RequestFactory().get('/')
        self.req.user = AnonymousUser()
        view = WebsiteSearchView.as_view()
        response = view(self.req)
        response.render()
        response.json = json.loads(response.content)
        return response

    def test_basic(self):
        response = self._test_get()
        eq_(response.status_code, 200)
        eq_(len(response.json['objects']), 1)


class TestWebsiteView(TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.test_data = {
            # XXX should share this with test_models
            'id': 1,
            'default_locale': 'en-US',
            'url': u'http://example.com/',
            'title': 'Test Site',
            'short_title': 'Tst St',
            'description': 'test site',
            'keywords': ['cvan', 'test'],
            'region_exclusions': [],
            'devices': [],
            'categories': [u'books', u'business'],
            'icon_type': 'icon',
            'icon_hash': 'blee',
        }
        self.store.sites[1] = MemoryWebsite(self.test_data)

    def test_get(self):
        with mock.patch('mkt.websites.views.store', self.store):
            url = reverse('api-v2:website-detail', kwargs={'pk': 1})
            r = self.client.get(url)
            eq_(r.status_code, 200)
            result = json.loads(r.content)
            for k in ('url', 'title', 'short_title', 'description', 'keywords',
                      'devices', 'categories'):
                eq_(self.test_data[k], result[k])
            eq_(result['icon_url'],
                'http://testserver/img/uploads/addon_icons/0/1-128.png'
                '?modified=blee')
