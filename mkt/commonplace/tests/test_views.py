from gzip import GzipFile
import json
from StringIO import StringIO

from django.conf import settings
from django.test.utils import override_settings

import mock
from jinja2.utils import escape
from nose import SkipTest
from nose.tools import eq_, ok_
from pyquery import PyQuery as pq

import amo.tests
from amo.utils import reverse


class BaseCommonPlaceTests(amo.tests.TestCase):

    def _test_url(self, url, url_kwargs=None):
        """Test that the given url can be requested, returns a 200, and returns
        a valid gzipped response when requested with Accept-Encoding over ssl.
        Return the result of a regular (non-gzipped) request."""
        if not url_kwargs:
            url_kwargs = {}
        res = self.client.get(url, url_kwargs, HTTP_ACCEPT_ENCODING='gzip',
            **{'wsgi.url_scheme': 'https'})
        eq_(res.status_code, 200)
        eq_(res['Content-Encoding'], 'gzip')
        eq_(sorted(res['Vary'].split(', ')),
            ['Accept-Encoding', 'Accept-Language', 'Cookie'])
        ungzipped_content = GzipFile('', 'r', 0, StringIO(res.content)).read()

        res = self.client.get(url, url_kwargs, **{'wsgi.url_scheme': 'https'})
        eq_(res.status_code, 200)
        eq_(sorted(res['Vary'].split(', ')),
            ['Accept-Encoding', 'Accept-Language', 'Cookie'])
        eq_(ungzipped_content, res.content)

        return res


class TestCommonplace(BaseCommonPlaceTests):

    def test_fireplace(self):
        res = self._test_url('/server.html')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'fireplace')
        self.assertContains(res, 'splash.css')
        self.assertContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')

    @mock.patch('mkt.commonplace.views.fxa_auth_info')
    def test_fireplace_firefox_accounts(self, mock_fxa):
        mock_fxa.return_value = ('fakestate', 'http://example.com/fakeauthurl')
        self.create_switch('firefox-accounts', db=True)
        res = self._test_url('/server.html')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'fireplace')
        self.assertContains(res, 'splash.css')
        self.assertNotContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')
        self.assertContains(res, 'fakestate')
        self.assertContains(res, 'http://example.com/fakeauthurl')
        self.assertContains(res,
             'data-waffle-switches="[&#34;firefox-accounts&#34;]"')

    def test_commbadge(self):
        res = self._test_url('/comm/')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'commbadge')
        self.assertNotContains(res, 'splash.css')
        self.assertContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')

    def test_rocketfuel(self):
        res = self._test_url('/curation/')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'rocketfuel')
        self.assertNotContains(res, 'splash.css')
        self.assertContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')

    def test_transonic(self):
        res = self._test_url('/curate/')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'transonic')
        self.assertNotContains(res, 'splash.css')
        self.assertContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')

    def test_discoplace(self):
        res = self._test_url('/discovery/')
        self.assertTemplateUsed(res, 'commonplace/index.html')
        self.assertEquals(res.context['repo'], 'discoplace')
        self.assertContains(res, 'splash.css')
        self.assertNotContains(res, 'login.persona.org/include.js')
        eq_(res['Cache-Control'], 'max-age=180')

    def test_fireplace_persona_js_not_included_on_firefox_os(self):
        for url in ('/server.html?mccs=blah',
                    '/server.html?mcc=blah&mnc=blah',
                    '/server.html?nativepersona=true'):
            res = self._test_url(url)
            self.assertNotContains(res, 'login.persona.org/include.js')

    @mock.patch('mkt.commonplace.views.fxa_auth_info')
    def test_fireplace_persona_not_included_firefox_accounts(self, mock_fxa):
        mock_fxa.return_value = ('fakestate', 'http://example.com/fakeauthurl')
        for url in ('/server.html',
                    '/server.html?mcc=blah',
                    '/server.html?mccs=blah',
                    '/server.html?mcc=blah&mnc=blah',
                    '/server.html?nativepersona=true'):
            res = self._test_url(url)
            self.assertNotContains(res, 'login.persona.org/include.js')

    def test_fireplace_persona_js_is_included_elsewhere(self):
        for url in ('/server.html', '/server.html?mcc=blah'):
            res = self._test_url(url)
            self.assertContains(res, 'login.persona.org/include.js" async')

    def test_rocketfuel_persona_js_is_included(self):
        for url in ('/curation/', '/curation/?nativepersona=true'):
            res = self._test_url(url)
            self.assertContains(res, 'login.persona.org/include.js" defer')

    @mock.patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_region_not_included_in_fireplace_if_sim_info(self, mock_region):
        test_region = mock.Mock()
        test_region.slug = 'testoland'
        mock_region.return_value = test_region
        for url in ('/server.html?mccs=blah',
                    '/server.html?mcc=blah&mnc=blah'):
            res = self._test_url(url)
            ok_('geoip_region' not in res.context, url)
            self.assertNotContains(res, 'data-region')

    @mock.patch('mkt.regions.middleware.RegionMiddleware.region_from_request')
    def test_region_included_in_fireplace_if_sim_info(self, mock_region):
        test_region = mock.Mock()
        test_region.slug = 'testoland'
        mock_region.return_value = test_region
        for url in ('/server.html?nativepersona=true',
                    '/server.html?mcc=blah',  # Incomplete info from SIM.
                    '/server.html',
                    '/server.html?'):
            res = self._test_url(url)
            self.assertEquals(res.context['geoip_region'], test_region)
            self.assertContains(res, 'data-region="testoland"')


class TestAppcacheManifest(BaseCommonPlaceTests):

    def test_no_repo(self):
        if 'fireplace' not in settings.COMMONPLACE_REPOS_APPCACHED:
            raise SkipTest

        res = self.client.get(reverse('commonplace.appcache'))
        eq_(res.status_code, 404)

    def test_bad_repo(self):
        if 'fireplace' not in settings.COMMONPLACE_REPOS_APPCACHED:
            raise SkipTest

        res = self.client.get(reverse('commonplace.appcache'),
                              {'repo': 'rocketfuel'})
        eq_(res.status_code, 404)

    @mock.patch('mkt.commonplace.views.get_build_id', new=lambda x: 'p00p')
    @mock.patch('mkt.commonplace.views.get_imgurls')
    def test_good_repo(self, get_imgurls_mock):
        if 'fireplace' not in settings.COMMONPLACE_REPOS_APPCACHED:
            raise SkipTest

        img = '/media/img/icons/eggs/h1.gif'
        get_imgurls_mock.return_value = [img]
        res = self._test_url(reverse('commonplace.appcache'),
                             {'repo': 'fireplace'})
        eq_(res.status_code, 200)
        assert '# BUILD_ID p00p' in res.content
        img = img.replace('/media/', '/media/fireplace/')
        assert img + '\n' in res.content


class TestIFrames(BaseCommonPlaceTests):
    def setUp(self):
        self.iframe_install_url = reverse('commonplace.iframe-install')
        self.potatolytics_url = reverse('commonplace.potatolytics')

    @override_settings(DOMAIN='marketplace.firefox.com')
    def test_basic(self):
        res = self._test_url(self.iframe_install_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace.firefox.com',
             'app://marketplace.firefox.com',
             'https://marketplace.firefox.com',
             'app://tarako.marketplace.firefox.com',
             'https://hello.firefox.com',
             'https://call.firefox.com'])

        res = self._test_url(self.potatolytics_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace.firefox.com',
             'app://marketplace.firefox.com',
             'https://marketplace.firefox.com',
             'app://tarako.marketplace.firefox.com'])

    @override_settings(DOMAIN='marketplace.allizom.org')
    def test_basic_stage(self):
        res = self._test_url(self.iframe_install_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace.allizom.org',
             'app://marketplace.allizom.org',
             'https://marketplace.allizom.org',
             'app://tarako.marketplace.allizom.org',
             'https://hello.firefox.com',
             'https://call.firefox.com'])

        res = self._test_url(self.potatolytics_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace.allizom.org',
             'app://marketplace.allizom.org',
             'https://marketplace.allizom.org',
             'app://tarako.marketplace.allizom.org'])

    @override_settings(DOMAIN='marketplace-dev.allizom.org')
    def test_basic_dev(self):
        res = self._test_url(self.iframe_install_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace-dev.allizom.org',
             'app://marketplace-dev.allizom.org',
             'https://marketplace-dev.allizom.org',
             'app://tarako.marketplace-dev.allizom.org',
             'http://localhost:8675',
             'https://localhost:8675',
             'http://localhost',
             'https://localhost',
             'http://mp.dev',
             'https://mp.dev',
             'https://hello.firefox.com',
             'https://call.firefox.com',
             'http://loop-webapp.dev.mozaws.net'])

        res = self._test_url(self.potatolytics_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.marketplace-dev.allizom.org',
             'app://marketplace-dev.allizom.org',
             'https://marketplace-dev.allizom.org',
             'app://tarako.marketplace-dev.allizom.org',
             'http://localhost:8675',
             'https://localhost:8675',
             'http://localhost',
             'https://localhost',
             'http://mp.dev',
             'https://mp.dev'])

    @override_settings(DOMAIN='example.com', DEBUG=True)
    def test_basic_debug_true(self):
        res = self._test_url(self.iframe_install_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.example.com',
             'app://example.com',
             'https://example.com',
             'app://tarako.example.com',
             'http://localhost:8675',
             'https://localhost:8675',
             'http://localhost',
             'https://localhost',
             'http://mp.dev',
             'https://mp.dev',
             'https://hello.firefox.com',
             'https://call.firefox.com',
             'http://loop-webapp.dev.mozaws.net'])

        res = self._test_url(self.potatolytics_url)
        whitelisted_origins = json.loads(res.context['whitelisted_origins'])
        eq_(whitelisted_origins,
            ['app://packaged.example.com',
             'app://example.com',
             'https://example.com',
             'app://tarako.example.com',
             'http://localhost:8675',
             'https://localhost:8675',
             'http://localhost',
             'https://localhost',
             'http://mp.dev',
             'https://mp.dev'])


class TestOpenGraph(amo.tests.TestCase):

    def _get_tags(self, res):
        """Returns title, image, description."""
        doc = pq(res.content)
        return (doc('[property="og:title"]').attr('content'),
                doc('[property="og:image"]').attr('content'),
                doc('[name="description"]').attr('content'))

    def test_basic(self):
        res = self.client.get(reverse('commonplace.fireplace'))
        title, image, description = self._get_tags(res)
        eq_(title, 'Firefox Marketplace')
        ok_(description.startswith('The Firefox Marketplace is'))

    def test_detail(self):
        app = amo.tests.app_factory(description='Awesome')
        res = self.client.get(reverse('detail', args=[app.app_slug]))
        title, image, description = self._get_tags(res)
        eq_(title, app.name)
        eq_(image, app.get_icon_url(64))
        eq_(description, app.description)

    def test_detail_dne(self):
        res = self.client.get(reverse('detail', args=['DO NOT EXISTS']))
        title, image, description = self._get_tags(res)
        eq_(title, 'Firefox Marketplace')
        ok_(description.startswith('The Firefox Marketplace is'))
