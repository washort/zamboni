import mock
from nose.tools import eq_

from mkt.site.tests import TestCase
from mkt.websites.models import Website
from mkt.websites.models import store, MemoryStore, MemoryWebsite
from mkt.websites.utils import website_factory


class TestWebsiteESIndexation(TestCase):
    @mock.patch('mkt.search.indexers.BaseIndexer.index_ids')
    def test_update_search_index(self, update_mock):
        website = website_factory()
        update_mock.assert_called_once_with([website.pk])

    @mock.patch('mkt.search.indexers.BaseIndexer.unindex')
    def test_delete_search_index(self, delete_mock):
        for x in xrange(4):
            website_factory()
        count = Website.objects.count()
        Website.objects.all().delete()
        eq_(delete_mock.call_count, count)


class _WebsiteModelTests(object):
    def test_fetch_simple(self):
        data = {
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
        self.create_website(**data)
        ws = self.store.fetch_visible_website(pk=1)
        eq_(ws.id, data['id'])
        eq_(ws.default_locale, data['default_locale'])
        eq_(ws.url, data['url'])
        eq_(ws.title, data['title'])
        eq_(ws.short_title, data['short_title'])
        eq_(ws.description, data['description'])
        eq_(ws.keywords, data['keywords'])
        eq_(ws.region_exclusions, data['region_exclusions'])
        eq_(ws.devices, data['devices'])
        eq_(ws.categories, data['categories'])
        eq_(ws.icon_url,
            'http://testserver/img/uploads/addon_icons/0/1-128.png'
            '?modified=blee')


class WebsiteDBTests(TestCase, _WebsiteModelTests):

    store = store

    def create_website(self, **data):
        if 'keywords' in data:
            kws = data.pop('keywords')
        else:
            kws = None
        ws = Website.objects.create(**data)
        if kws:
            for k in kws:
                ws._keywords.create(tag_text=k)
        return ws


class WebsiteMemoryTests(TestCase, _WebsiteModelTests):
    def setUp(self):
        self.store = MemoryStore()

    def create_website(self, **data):
        ws = MemoryWebsite(data)
        self.store.sites[data['id']] = ws
        return ws
