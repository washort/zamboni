# -*- coding: utf-8 -*-
import mimetypes
import os
from datetime import datetime, timedelta
from urlparse import urljoin

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import encoding

import jingo
import test_utils
from mock import Mock, patch
from nose.tools import eq_
from pyquery import PyQuery

import amo
import amo.tests
from amo import urlresolvers, utils, helpers
from amo.utils import guard, ImageCheck, Message, Token
from versions.models import License


def render(s, context={}):
    t = jingo.env.from_string(s)
    return t.render(**context)


def test_strip_html():
    eq_('Hey Brother!', render('{{ "Hey <b>Brother!</b>"|strip_html }}'))


def test_currencyfmt():
    eq_(helpers.currencyfmt(None, 'USD'), '')
    eq_(helpers.currencyfmt(5, 'USD'), '$5.00')


def test_strip_html_none():
    eq_('', render('{{ a|strip_html }}', {'a': None}))
    eq_('', render('{{ a|strip_html(True) }}', {'a': None}))


def test_strip_controls():
    # We want control codes like \x0c to disappear.
    eq_('I ove you', helpers.strip_controls('I \x0cove you'))


def test_finalize():
    """We want None to show up as ''.  We do this in JINJA_CONFIG."""
    eq_('', render('{{ x }}', {'x': None}))


def test_slugify_spaces():
    """We want slugify to preserve spaces, but not at either end."""
    eq_(utils.slugify(' b ar '), 'b-ar')
    eq_(utils.slugify(' b ar ', spaces=True), 'b ar')
    eq_(utils.slugify(' b  ar ', spaces=True), 'b  ar')


def test_page_title():
    request = Mock()
    request.APP = amo.THUNDERBIRD
    title = 'Oh hai!'
    s = render('{{ page_title("%s") }}' % title, {'request': request})
    eq_(s, '%s :: Add-ons for Thunderbird' % title)

    # pages without app should show a default
    request.APP = None
    s = render('{{ page_title("%s") }}' % title, {'request': request})
    eq_(s, '%s :: Add-ons' % title)

    # Check the dirty unicodes.
    request.APP = amo.FIREFOX
    s = render('{{ page_title(x) }}',
               {'request': request,
                'x': encoding.smart_str(u'\u05d0\u05d5\u05e1\u05e3')})


class TestBreadcrumbs(object):

    def setUp(self):
        self.req_noapp = Mock()
        self.req_noapp.APP = None
        self.req_app = Mock()
        self.req_app.APP = amo.FIREFOX

    def test_no_app(self):
        s = render('{{ breadcrumbs() }}', {'request': self.req_noapp})
        doc = PyQuery(s)
        crumbs = doc('li>a')
        eq_(len(crumbs), 1)
        eq_(crumbs.text(), 'Add-ons')
        eq_(crumbs.attr('href'), urlresolvers.reverse('home'))

    def test_with_app(self):
        s = render('{{ breadcrumbs() }}', {'request': self.req_app})
        doc = PyQuery(s)
        crumbs = doc('li>a')
        eq_(len(crumbs), 1)
        eq_(crumbs.text(), 'Add-ons for Firefox')
        eq_(crumbs.attr('href'), urlresolvers.reverse('home'))

    def test_no_add_default(self):
        s = render('{{ breadcrumbs(add_default=False) }}',
                   {'request': self.req_app})
        eq_(len(s), 0)

    def test_items(self):
        s = render("""{{ breadcrumbs([('/foo', 'foo'),
                                      ('/bar', 'bar')],
                                     add_default=False) }}'""",
                   {'request': self.req_app})
        doc = PyQuery(s)
        crumbs = doc('li>a')
        eq_(len(crumbs), 2)
        eq_(crumbs.eq(0).text(), 'foo')
        eq_(crumbs.eq(0).attr('href'), '/foo')
        eq_(crumbs.eq(1).text(), 'bar')
        eq_(crumbs.eq(1).attr('href'), '/bar')

    def test_items_with_default(self):
        s = render("""{{ breadcrumbs([('/foo', 'foo'),
                                      ('/bar', 'bar')]) }}'""",
                   {'request': self.req_app})
        doc = PyQuery(s)
        crumbs = doc('li>a')
        eq_(len(crumbs), 3)
        eq_(crumbs.eq(1).text(), 'foo')
        eq_(crumbs.eq(1).attr('href'), '/foo')
        eq_(crumbs.eq(2).text(), 'bar')
        eq_(crumbs.eq(2).attr('href'), '/bar')

    def test_truncate(self):
        s = render("""{{ breadcrumbs([('/foo', 'abcd efghij'),],
                                     crumb_size=5) }}'""",
                   {'request': self.req_app})
        doc = PyQuery(s)
        crumbs = doc('li>a')
        eq_('abcd ...', crumbs.eq(1).text())

    def test_xss(self):
        s = render("{{ breadcrumbs([('/foo', '<script>')]) }}",
                   {'request': self.req_app})
        assert '&lt;script&gt;' in s, s
        assert '<script>' not in s


@patch('amo.helpers.urlresolvers.reverse')
def test_url(mock_reverse):
    render('{{ url("viewname", 1, z=2) }}')
    mock_reverse.assert_called_with('viewname', args=(1,), kwargs={'z': 2},
                                    add_prefix=True)

    render('{{ url("viewname", 1, z=2, host="myhost") }}')
    mock_reverse.assert_called_with('viewname', args=(1,), kwargs={'z': 2},
                                    add_prefix=True)


def test_url_src():
    s = render('{{ url("addons.detail", "a3615", src="xxx") }}')
    assert s.endswith('?src=xxx')


def test_urlparams():
    url = '/en-US/firefox/themes/category'
    c = {'base': url,
         'base_frag': url + '#hash',
         'base_query': url + '?x=y',
         'sort': 'name', 'frag': 'frag'}

    # Adding a query.
    s = render('{{ base_frag|urlparams(sort=sort) }}', c)
    eq_(s, '%s?sort=name#hash' % url)

    # Adding a fragment.
    s = render('{{ base|urlparams(frag) }}', c)
    eq_(s, '%s#frag' % url)

    # Replacing a fragment.
    s = render('{{ base_frag|urlparams(frag) }}', c)
    eq_(s, '%s#frag' % url)

    # Adding query and fragment.
    s = render('{{ base_frag|urlparams(frag, sort=sort) }}', c)
    eq_(s, '%s?sort=name#frag' % url)

    # Adding query with existing params.
    s = render('{{ base_query|urlparams(frag, sort=sort) }}', c)
    eq_(s, '%s?sort=name&amp;x=y#frag' % url)

    # Replacing a query param.
    s = render('{{ base_query|urlparams(frag, x="z") }}', c)
    eq_(s, '%s?x=z#frag' % url)

    # Params with value of None get dropped.
    s = render('{{ base|urlparams(sort=None) }}', c)
    eq_(s, url)

    # Removing a query
    s = render('{{ base_query|urlparams(x=None) }}', c)
    eq_(s, url)


def test_urlparams_unicode():
    url = u'/xx?evil=reco\ufffd\ufffd\ufffd\u02f5'
    utils.urlparams(url)


class TestSharedURL(amo.tests.TestCase):

    def setUp(self):
        self.webapp = Mock()
        self.webapp.type = amo.ADDON_WEBAPP
        self.webapp.app_slug = 'webapp'

        self.addon = Mock()
        self.addon.type = amo.ADDON_EXTENSION
        self.addon.slug = 'addon'
        self.addon.is_webapp.return_value = False

    def test_addonurl(self):
        expected = '/en-US/firefox/addon/addon/'
        eq_(helpers.shared_url('addons.detail', self.addon), expected)
        eq_(helpers.shared_url('apps.detail', self.addon), expected)
        eq_(helpers.shared_url('detail', self.addon), expected)
        eq_(helpers.shared_url('detail', self.addon, add_prefix=False),
            '/addon/addon/')
        eq_(helpers.shared_url('reviews.detail', self.addon, 1,
                               add_prefix=False),
            '/addon/addon/reviews/1/')


def test_isotime():
    time = datetime(2009, 12, 25, 10, 11, 12)
    s = render('{{ d|isotime }}', {'d': time})
    eq_(s, '2009-12-25T18:11:12Z')
    s = render('{{ d|isotime }}', {'d': None})
    eq_(s, '')


def test_epoch():
    time = datetime(2009, 12, 25, 10, 11, 12)
    s = render('{{ d|epoch }}', {'d': time})
    eq_(s, '1261764672')
    s = render('{{ d|epoch }}', {'d': None})
    eq_(s, '')


def test_locale_url():
    rf = test_utils.RequestFactory()
    request = rf.get('/de', SCRIPT_NAME='/z')
    prefixer = urlresolvers.Prefixer(request)
    urlresolvers.set_url_prefix(prefixer)
    s = render('{{ locale_url("mobile") }}')
    eq_(s, '/z/de/mobile')


def test_external_url():
    redirect_url = settings.REDIRECT_URL
    secretkey = settings.REDIRECT_SECRET_KEY
    settings.REDIRECT_URL = 'http://example.net'
    settings.REDIRECT_SECRET_KEY = 'sekrit'

    try:
        myurl = 'http://example.com'
        s = render('{{ "%s"|external_url }}' % myurl)
        eq_(s, urlresolvers.get_outgoing_url(myurl))
    finally:
        settings.REDIRECT_URL = redirect_url
        settings.REDIRECT_SECRET_KEY = secretkey


@patch('amo.helpers.urlresolvers.get_outgoing_url')
def test_linkify_bounce_url_callback(mock_get_outgoing_url):
    mock_get_outgoing_url.return_value = 'bar'

    res = urlresolvers.linkify_bounce_url_callback({'href': 'foo'})

    # Make sure get_outgoing_url was called.
    eq_(res, {'href': 'bar'})
    mock_get_outgoing_url.assert_called_with('foo')


@patch('amo.helpers.urlresolvers.linkify_bounce_url_callback')
def test_linkify_with_outgoing(mock_linkify_bounce_url_callback):
    def side_effect(attrs, new=False):
        attrs['href'] = 'bar'
        return attrs

    mock_linkify_bounce_url_callback.side_effect = side_effect

    # Without nofollow.
    res = urlresolvers.linkify_with_outgoing('http://example.com',
                                             nofollow=False)
    eq_(res, '<a href="bar">http://example.com</a>')

    # With nofollow (default).
    res = urlresolvers.linkify_with_outgoing('http://example.com')
    eq_(res, '<a href="bar" rel="nofollow">http://example.com</a>')

    res = urlresolvers.linkify_with_outgoing('http://example.com',
                                             nofollow=True)
    eq_(res, '<a href="bar" rel="nofollow">http://example.com</a>')


class TestLicenseLink(amo.tests.TestCase):

    def test_license_link(self):
        mit = License.objects.create(
            name='MIT/X11 License', builtin=6, url='http://m.it')
        copyright = License.objects.create(
            name='All Rights Reserved', icons='copyr', builtin=7)
        cc = License.objects.create(
            name='Creative Commons', url='http://cre.at', builtin=8,
            some_rights=True, icons='cc-attrib cc-noncom cc-share')
        cc.save()
        expected = {
            mit: (
                '<ul class="license"><li class="text">'
                '<a href="http://m.it">MIT/X11 License</a></li></ul>'),
            copyright: (
                '<ul class="license"><li class="icon copyr"></li>'
                '<li class="text">All Rights Reserved</li></ul>'),
            cc: (
                '<ul class="license"><li class="icon cc-attrib"></li>'
                '<li class="icon cc-noncom"></li><li class="icon cc-share">'
                '</li><li class="text"><a href="http://cre.at" '
                'title="Creative Commons">Some rights reserved</a></li></ul>'),
        }
        for lic, ex in expected.items():
            s = render('{{ license_link(lic) }}', {'lic': lic})
            s = ''.join([s.strip() for s in s.split('\n')])
            eq_(s, ex)

    def test_theme_license_link(self):
        s = render('{{ license_link(lic) }}', {'lic': amo.LICENSE_COPYRIGHT})

        ul = PyQuery(s)('.license')
        eq_(ul.find('.icon').length, 1)
        eq_(ul.find('.icon.copyr').length, 1)

        text = ul.find('.text')
        eq_(text.find('a').length, 0)
        eq_(text.text(), 'All Rights Reserved')

        s = render('{{ license_link(lic) }}', {'lic': amo.LICENSE_CC_BY_NC_SA})

        ul = PyQuery(s)('.license')
        eq_(ul.find('.icon').length, 3)
        eq_(ul.find('.icon.cc-attrib').length, 1)
        eq_(ul.find('.icon.cc-noncom').length, 1)
        eq_(ul.find('.icon.cc-share').length, 1)

        link = ul.find('.text a')
        eq_(link.find('a').length, 0)
        eq_(link.text(), 'Some rights reserved')
        eq_(link.attr('href'), amo.LICENSE_CC_BY_NC_SA.url)

    def test_license_link_xss(self):
        mit = License.objects.create(
            name='<script>', builtin=6, url='<script>')
        copyright = License.objects.create(
            name='<script>', icons='<script>', builtin=7)
        cc = License.objects.create(
            name='<script>', url='<script>', builtin=8,
            some_rights=True, icons='<script> cc-noncom cc-share')
        cc.save()
        expected = {
            mit: (
                '<ul class="license"><li class="text">'
                '<a href="&lt;script&gt;">&lt;script&gt;</a></li></ul>'),
            copyright: (
                '<ul class="license"><li class="icon &lt;script&gt;"></li>'
                '<li class="text">&lt;script&gt;</li></ul>'),
            cc: (
                '<ul class="license"><li class="icon &lt;script&gt;"></li>'
                '<li class="icon cc-noncom"></li><li class="icon cc-share">'
                '</li><li class="text"><a href="&lt;script&gt;" '
                'title="&lt;script&gt;">Some rights reserved</a></li></ul>'),
        }
        for lic, ex in expected.items():
            s = render('{{ license_link(lic) }}', {'lic': lic})
            s = ''.join([s.strip() for s in s.split('\n')])
            eq_(s, ex)


def get_image_path(name):
    return os.path.join(settings.ROOT, 'apps', 'amo', 'tests', 'images', name)


def get_uploaded_file(name):
    data = open(get_image_path(name)).read()
    return SimpleUploadedFile(name, data,
                              content_type=mimetypes.guess_type(name)[0])


class TestAnimatedImages(amo.tests.TestCase):

    def test_animated_images(self):
        img = ImageCheck(open(get_image_path('animated.png')))
        assert img.is_animated()
        img = ImageCheck(open(get_image_path('non-animated.png')))
        assert not img.is_animated()

        img = ImageCheck(open(get_image_path('animated.gif')))
        assert img.is_animated()
        img = ImageCheck(open(get_image_path('non-animated.gif')))
        assert not img.is_animated()

    def test_junk(self):
        img = ImageCheck(open(__file__, 'rb'))
        assert not img.is_image()
        img = ImageCheck(open(get_image_path('non-animated.gif')))
        assert img.is_image()


class TestToken(amo.tests.TestCase):

    def test_token_pop(self):
        new = Token()
        new.save()
        assert Token.pop(new.token)
        assert not Token.pop(new.token)

    def test_token_valid(self):
        new = Token()
        new.save()
        assert Token.valid(new.token)

    def test_token_fails(self):
        assert not Token.pop('some-random-token')

    def test_token_ip(self):
        new = Token(data='127.0.0.1')
        new.save()
        assert Token.valid(new.token, '127.0.0.1')

    def test_token_no_ip_invalid(self):
        new = Token()
        assert not Token.valid(new.token, '255.255.255.0')

    def test_token_bad_ip_invalid(self):
        new = Token(data='127.0.0.1')
        new.save()
        assert not Token.pop(new.token, '255.255.255.0')
        assert Token.pop(new.token, '127.0.0.1')

    def test_token_well_formed(self):
        new = Token('some badly formed token')
        assert not new.well_formed()


class TestMessage(amo.tests.TestCase):

    def test_message_save(self):
        new = Message('abc')
        new.save('123')

        new = Message('abc')
        eq_(new.get(), '123')

    def test_message_expires(self):
        new = Message('abc')
        new.save('123')
        cache.clear()

        new = Message('abc')
        eq_(new.get(), None)

    def test_message_get_delete(self):
        new = Message('abc')
        new.save('123')

        new = Message('abc')
        eq_(new.get(delete=False), '123')
        eq_(new.get(delete=True), '123')
        eq_(new.get(), None)

    def test_guard(self):
        with guard('abc') as locked:
            eq_(locked, False)
            eq_(Message('abc').get(), True)

    def test_guard_copes(self):
        try:
            with guard('abc'):
                1 / 0
        except ZeroDivisionError:
            pass

        eq_(Message('abc').get(), None)

    def test_guard_deletes(self):
        with guard('abc'):
            pass
        eq_(Message('abc').get(), None)

    def test_guard_blocks(self):
        Message('abc').save(True)
        with guard('abc') as locked:
            eq_(locked, True)


def test_site_nav():
    r = Mock()
    r.APP = amo.FIREFOX
    assert 'id="site-nav"' in helpers.site_nav({'request': r})


def test_jinja_trans_monkeypatch():
    # This tests the monkeypatch in manage.py that prevents localizers from
    # taking us down.
    render('{% trans come_on=1 %}% (come_on)s{% endtrans %}')
    render('{% trans come_on=1 %}%(come_on){% endtrans %}')
    render('{% trans come_on=1 %}%(come_on)z{% endtrans %}')


def test_absolutify():
    eq_(helpers.absolutify('/woo'), urljoin(settings.SITE_URL, '/woo'))
    eq_(helpers.absolutify('https://addons.mozilla.org'),
        'https://addons.mozilla.org')


def test_timesince():
    month_ago = datetime.now() - timedelta(days=30)
    eq_(helpers.timesince(month_ago), u'1 month ago')
    eq_(helpers.timesince(None), u'')
