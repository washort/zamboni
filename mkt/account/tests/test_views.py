# -*- coding: utf-8 -*-
import collections
import json
import uuid
from urlparse import urlparse

from django.conf import settings
from django.core import mail
from django.core.urlresolvers import reverse
from django.http import QueryDict
from django.utils.http import urlencode

from mock import patch, Mock
from nose.tools import eq_, ok_

import amo
from amo.tests import TestCase, app_factory
from amo.utils import urlparams

from mkt.account.views import MineMixin
from mkt.api.tests.test_oauth import RestOAuth
from mkt.constants.apps import INSTALL_TYPE_REVIEWER
from mkt.site.fixtures import fixture
from mkt.webapps.models import Installed
from mkt.users.models import UserProfile


class TestPotatoCaptcha(object):

    def _test_bad_api_potato_data(self, response, data=None):
        if not data:
            data = json.loads(response.content)
        eq_(400, response.status_code)
        ok_('non_field_errors' in data)
        eq_(data['non_field_errors'], [u'Form could not be submitted.'])


class FakeResourceBase(object):
    pass


class FakeResource(MineMixin, FakeResourceBase):
    def __init__(self, pk, request):
        self.kwargs = {'pk': pk}
        self.request = request


class TestMine(TestCase):
    fixtures = fixture('user_2519')

    def setUp(self):
        self.request = Mock()
        self.request.user = UserProfile.objects.get(id=2519)

    @patch.object(FakeResourceBase, 'get_object', create=True)
    def test_get_object(self, mocked_get_object):
        r = FakeResource(999, self.request)
        r.get_object()
        eq_(r.kwargs['pk'], 999)

        r = FakeResource('mine', self.request)
        r.get_object()
        eq_(r.kwargs['pk'], 2519)


class TestPermission(RestOAuth):
    fixtures = fixture('user_2519', 'user_10482')

    def setUp(self):
        super(TestPermission, self).setUp()
        self.get_url = reverse('account-permissions', kwargs={'pk': 2519})
        self.user = UserProfile.objects.get(pk=2519)

    def test_has_cors(self):
        self.assertCORS(self.client.get(self.get_url), 'get')

    def test_verbs(self):
        self._allowed_verbs(self.get_url, ('get'))

    def test_other(self):
        self.get_url = reverse('account-permissions', kwargs={'pk': 10482})
        eq_(self.client.get(self.get_url).status_code, 403)

    def test_no_permissions(self):
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200, res.content)
        self.assertSetEqual(
            ['admin', 'developer', 'localizer', 'lookup', 'curator',
             'reviewer', 'webpay', 'stats', 'revenue_stats'],
            res.json['permissions'].keys()
        )
        ok_(not all(res.json['permissions'].values()))

    def test_some_permission(self):
        self.grant_permission(self.user, 'Localizers:%')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['localizer'])

    def test_mine(self):
        self.get_url = reverse('account-permissions', kwargs={'pk': 'mine'})
        self.test_some_permission()

    def test_mine_anon(self):
        self.get_url = reverse('account-permissions', kwargs={'pk': 'mine'})
        res = self.anon.get(self.get_url)
        eq_(res.status_code, 403)

    def test_publisher(self):
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(not res.json['permissions']['curator'])

    def test_publisher_ok(self):
        self.grant_permission(self.user, 'Collections:Curate')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['curator'])

    def test_feed_publisher_ok(self):
        self.grant_permission(self.user, 'Feed:Curate')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['curator'])

    def test_webpay(self):
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(not res.json['permissions']['webpay'])

    def test_webpay_ok(self):
        self.grant_permission(self.user, 'ProductIcon:Create')
        self.grant_permission(self.user, 'Transaction:NotifyFailure')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['webpay'])

    def test_stats(self):
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(not res.json['permissions']['stats'])

    def test_stats_ok(self):
        self.grant_permission(self.user, 'Stats:View')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['stats'])

    def test_revenue_stats(self):
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(not res.json['permissions']['revenue_stats'])

    def test_revenue_stats_ok(self):
        self.grant_permission(self.user, 'RevenueStats:View')
        res = self.client.get(self.get_url)
        eq_(res.status_code, 200)
        ok_(res.json['permissions']['revenue_stats'])


class TestAccount(RestOAuth):
    fixtures = fixture('user_2519', 'user_10482', 'webapp_337141')

    def setUp(self):
        super(TestAccount, self).setUp()
        self.url = reverse('account-settings', kwargs={'pk': 2519})
        self.user = UserProfile.objects.get(pk=2519)

    def test_has_cors(self):
        self.assertCORS(self.client.get(self.url), 'get', 'patch', 'put')

    def test_verbs(self):
        self._allowed_verbs(self.url, ('get', 'patch', 'put'))

    def test_not_allowed(self):
        eq_(self.anon.get(self.url).status_code, 403)

    def test_allowed(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200, res.content)
        data = json.loads(res.content)
        eq_(data['display_name'], self.user.display_name)

    def test_other(self):
        url = reverse('account-settings', kwargs={'pk': 10482})
        eq_(self.client.get(url).status_code, 403)

    def test_own(self):
        url = reverse('account-settings', kwargs={'pk': 'mine'})
        res = self.client.get(url)
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        eq_(data['display_name'], self.user.display_name)

    def test_own_empty(self):
        self.user.update(display_name='')
        url = reverse('account-settings', kwargs={'pk': 'mine'})
        res = self.client.get(url)
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        eq_(data['display_name'], self.user.username)

    def test_patch(self):
        res = self.client.patch(self.url,
                                data=json.dumps({'display_name': 'foo'}))
        eq_(res.status_code, 200)
        user = UserProfile.objects.get(pk=self.user.pk)
        eq_(user.display_name, 'foo')

    def test_patch_empty(self):
        res = self.client.patch(self.url,
                                data=json.dumps({'display_name': None}))
        eq_(res.status_code, 400)
        data = json.loads(res.content)
        eq_(data['display_name'], [u'This field is required'])

        res = self.client.patch(self.url,
                                data=json.dumps({'display_name': ''}))
        eq_(res.status_code, 400)
        data = json.loads(res.content)
        eq_(data['display_name'], [u'This field is required'])

    def test_put(self):
        res = self.client.put(self.url,
                              data=json.dumps({'display_name': 'foo'}))
        eq_(res.status_code, 200)
        user = UserProfile.objects.get(pk=self.user.pk)
        eq_(user.display_name, 'foo')
        eq_(user.username, self.user.username)  # Did not change.

    def test_patch_extra_fields(self):
        res = self.client.patch(self.url,
                                data=json.dumps({'display_name': 'foo',
                                                 'username': 'bob'}))
        eq_(res.status_code, 200)
        user = UserProfile.objects.get(pk=self.user.pk)
        eq_(user.display_name, 'foo')  # Got changed successfully.
        eq_(user.username, self.user.username)  # Did not change.

    def test_patch_other(self):
        url = reverse('account-settings', kwargs={'pk': 10482})
        res = self.client.patch(url, data=json.dumps({'display_name': 'foo'}))
        eq_(res.status_code, 403)


class TestInstalled(RestOAuth):
    fixtures = fixture('user_2519', 'user_10482', 'webapp_337141')

    def setUp(self):
        super(TestInstalled, self).setUp()
        self.list_url = reverse('installed-apps')
        self.user = UserProfile.objects.get(pk=2519)

    def test_has_cors(self):
        self.assertCORS(self.client.get(self.list_url), 'get')

    def test_verbs(self):
        self._allowed_verbs(self.list_url, ('get'))

    def test_not_allowed(self):
        eq_(self.anon.get(self.list_url).status_code, 403)

    def test_installed(self):
        ins = Installed.objects.create(user=self.user, addon_id=337141)
        res = self.client.get(self.list_url)
        eq_(res.status_code, 200, res.content)
        data = json.loads(res.content)
        eq_(data['meta']['total_count'], 1)
        eq_(data['objects'][0]['id'], ins.addon.pk)
        eq_(data['objects'][0]['user'],
            {'developed': False, 'purchased': False, 'installed': True})

    def test_installed_pagination(self):
        ins1 = Installed.objects.create(user=self.user, addon=app_factory())
        ins1.update(created=self.days_ago(1))
        ins2 = Installed.objects.create(user=self.user, addon=app_factory())
        ins2.update(created=self.days_ago(2))
        ins3 = Installed.objects.create(user=self.user, addon=app_factory())
        ins3.update(created=self.days_ago(3))
        res = self.client.get(self.list_url, {'limit': 2})
        eq_(res.status_code, 200)
        data = json.loads(res.content)

        eq_(len(data['objects']), 2)
        eq_(data['objects'][0]['id'], ins1.addon.id)
        eq_(data['objects'][1]['id'], ins2.addon.id)
        eq_(data['meta']['total_count'], 3)
        eq_(data['meta']['limit'], 2)
        eq_(data['meta']['previous'], None)
        eq_(data['meta']['offset'], 0)
        next = urlparse(data['meta']['next'])
        eq_(next.path, self.list_url)
        eq_(QueryDict(next.query).dict(), {u'limit': u'2', u'offset': u'2'})

        res = self.client.get(self.list_url, {'limit': 2, 'offset': 2})
        eq_(res.status_code, 200)
        data = json.loads(res.content)

        eq_(len(data['objects']), 1)
        eq_(data['objects'][0]['id'], ins3.addon.id)
        eq_(data['meta']['total_count'], 3)
        eq_(data['meta']['limit'], 2)
        prev = urlparse(data['meta']['previous'])
        eq_(next.path, self.list_url)
        eq_(QueryDict(prev.query).dict(), {u'limit': u'2', u'offset': u'0'})
        eq_(data['meta']['offset'], 2)
        eq_(data['meta']['next'], None)

    def test_installed_order(self):
        # Should be reverse chronological order.
        ins1 = Installed.objects.create(user=self.user, addon=app_factory())
        ins1.update(created=self.days_ago(1))
        ins2 = Installed.objects.create(user=self.user, addon=app_factory())
        ins2.update(created=self.days_ago(2))
        res = self.client.get(self.list_url)
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        eq_(len(data['objects']), 2)
        eq_(data['objects'][0]['id'], ins1.addon.id)
        eq_(data['objects'][1]['id'], ins2.addon.id)

    def not_there(self):
        res = self.client.get(self.list_url)
        eq_(res.status_code, 200, res.content)
        data = json.loads(res.content)
        eq_(data['meta']['total_count'], 0)

    def test_installed_other(self):
        Installed.objects.create(user_id=10482, addon_id=337141)
        self.not_there()

    def test_installed_reviewer(self):
        Installed.objects.create(user=self.user, addon_id=337141,
                                 install_type=INSTALL_TYPE_REVIEWER)
        self.not_there()


class FakeUUID(object):
    hex = '000000'


@patch.object(settings, 'SECRET_KEY', 'gubbish')
class TestLoginHandler(TestCase):

    def setUp(self):
        super(TestLoginHandler, self).setUp()
        self.url = reverse('account-login')
        self.logout_url = reverse('account-logout')

    def post(self, data):
        return self.client.post(self.url, json.dumps(data),
                                content_type='application/json')

    @patch.object(uuid, 'uuid4', FakeUUID)
    @patch('requests.post')
    def _test_login(self, http_request):
        FakeResponse = collections.namedtuple('FakeResponse',
                                              'status_code json')
        http_request.return_value = FakeResponse(
            200, lambda: {'status': 'okay', 'email': 'cvan@mozilla.com'})
        res = self.post({'assertion': 'fake-assertion',
                         'audience': 'fakeamo.org'})
        eq_(res.status_code, 201)
        data = json.loads(res.content)
        eq_(data['token'],
            'cvan@mozilla.com,95c9063d9f249aacfe5697fc83192ed6480c01463e2a80b3'
            '5af5ecaef11754700f4be33818d0e83a0cfc2cab365d60ba53b3c2b9f8f6589d1'
            'c43e9bbb876eef0,000000')

        return data

    def test_login_new_user_success(self):
        data = self._test_login()
        ok_(not any(data['permissions'].values()))

    def test_login_existing_user_success(self):
        profile = UserProfile.objects.create(email='cvan@mozilla.com')
        self.grant_permission(profile, 'Apps:Review')

        data = self._test_login()
        eq_(data['permissions'],
            {'admin': False,
             'developer': False,
             'localizer': False,
             'lookup': False,
             'curator': False,
             'reviewer': True,
             'webpay': False,
             'stats': False,
             'revenue_stats': False})
        eq_(data['apps']['installed'], [])
        eq_(data['apps']['purchased'], [])
        eq_(data['apps']['developed'], [])

    @patch('mkt.users.models.UserProfile.purchase_ids')
    def test_relevant_apps(self, purchase_ids):
        profile = UserProfile.objects.create(email='cvan@mozilla.com')
        purchased_app = app_factory()
        purchase_ids.return_value = [purchased_app.pk]
        developed_app = app_factory()
        developed_app.addonuser_set.create(user=profile)
        installed_app = app_factory()
        installed_app.installed.create(user=profile)

        data = self._test_login()
        eq_(data['apps']['installed'], [installed_app.pk])
        eq_(data['apps']['purchased'], [purchased_app.pk])
        eq_(data['apps']['developed'], [developed_app.pk])

    @patch('requests.post')
    def test_login_failure(self, http_request):
        FakeResponse = collections.namedtuple('FakeResponse',
                                              'status_code json')
        http_request.return_value = FakeResponse(
            200, lambda: {'status': 'busted'})
        res = self.post({'assertion': 'fake-assertion',
                         'audience': 'fakeamo.org'})
        eq_(res.status_code, 403)

    def test_login_empty(self):
        res = self.post({})
        data = json.loads(res.content)
        eq_(res.status_code, 400)
        assert 'assertion' in data
        assert 'apps' not in data

    def test_logout(self):
        UserProfile.objects.create(email='cvan@mozilla.com')
        data = self._test_login()

        r = self.client.delete(
            urlparams(self.logout_url, _user=data['token']),
            content_type='application/json')
        eq_(r.status_code, 204)


@patch.object(settings, 'SECRET_KEY', 'gubbish')
class TestFxaLoginHandler(TestCase):

    def setUp(self):
        super(TestFxaLoginHandler, self).setUp()
        self.url = reverse('fxa-account-login')
        self.logout_url = reverse('account-logout')

    def post(self, data):
        return self.client.post(self.url, json.dumps(data),
                                content_type='application/json')

    @patch.object(uuid, 'uuid4', FakeUUID)
    @patch('requests.post')
    def _test_login(self, http_request):
        with patch('mkt.account.views.get_fxa_session') as get_session:
            m = get_session()
            m.fetch_token.return_value = {'access_token': 'fake'}
            m.post().json.return_value = {
                'user': 'fake-uid',
                'email': 'cvan@mozilla.com'
            }
            res = self.post({
                'auth_response': 'https://testserver/?access_token=fake-token&'
                                 'code=coed&state=fake-state',
                'state': 'fake-state'})
            eq_(res.status_code, 201)
            data = json.loads(res.content)
            eq_(data['token'],
                'cvan@mozilla.com,95c9063d9f249aacfe5697fc83192ed6480c01463e2a'
                '80b35af5ecaef11754700f4be33818d0e83a0cfc2cab365d60ba53b3c2b9f'
                '8f6589d1c43e9bbb876eef0,000000')
            return data

    def test_login_new_user_success(self):
        data = self._test_login()
        ok_(not any(data['permissions'].values()))

    def test_login_existing_user_uid_success(self):
        profile = UserProfile.objects.create(username='fake-uid')
        self.grant_permission(profile, 'Apps:Review')

        data = self._test_login()
        eq_(profile.reload().source, amo.LOGIN_SOURCE_FXA)
        eq_(data['permissions'],
            {'admin': False,
             'developer': False,
             'localizer': False,
             'lookup': False,
             'curator': False,
             'reviewer': True,
             'webpay': False,
             'stats': False,
             'revenue_stats': False})
        eq_(data['apps']['installed'], [])
        eq_(data['apps']['purchased'], [])
        eq_(data['apps']['developed'], [])

        # Ensure user profile got updated with email.
        eq_(profile.reload().email, 'cvan@mozilla.com')

    def test_login_existing_user_email_success(self):
        profile = UserProfile.objects.create(email='cvan@mozilla.com')
        self.grant_permission(profile, 'Apps:Review')

        data = self._test_login()
        eq_(profile.reload().source, amo.LOGIN_SOURCE_FXA)
        eq_(data['permissions'],
            {'admin': False,
             'developer': False,
             'localizer': False,
             'lookup': False,
             'curator': False,
             'reviewer': True,
             'webpay': False,
             'stats': False,
             'revenue_stats': False})
        eq_(data['apps']['installed'], [])
        eq_(data['apps']['purchased'], [])
        eq_(data['apps']['developed'], [])

        # Ensure user profile got updated with FxA UID.
        eq_(profile.reload().username, 'fake-uid')

    @patch('mkt.users.models.UserProfile.purchase_ids')
    def test_relevant_apps(self, purchase_ids):
        profile = UserProfile.objects.create(email='cvan@mozilla.com')
        purchased_app = app_factory()
        purchase_ids.return_value = [purchased_app.pk]
        developed_app = app_factory()
        developed_app.addonuser_set.create(user=profile)
        installed_app = app_factory()
        installed_app.installed.create(user=profile)

        data = self._test_login()
        eq_(data['apps']['installed'], [installed_app.pk])
        eq_(data['apps']['purchased'], [purchased_app.pk])
        eq_(data['apps']['developed'], [developed_app.pk])

    @patch('requests.post')
    def test_login_failure(self, http_request):
        with patch('mkt.account.views.get_fxa_session') as get_session:
            m = get_session()
            m.fetch_token.return_value = {'access_token': 'fake'}
            m.post().json.return_value = {'error': 'busted'}
            res = self.post({'auth_response': 'x',
                             'state': 'y'})
            eq_(res.status_code, 403)

    def test_login_empty(self):
        res = self.post({})
        data = json.loads(res.content)
        eq_(res.status_code, 400)
        assert 'auth_response' in data
        assert 'apps' not in data

    def test_login_settings(self):
        data = self._test_login()
        eq_(data['settings']['source'], 'firefox-accounts')

    def test_logout(self):
        UserProfile.objects.create(email='cvan@mozilla.com')
        data = self._test_login()

        r = self.client.delete(
            urlparams(self.logout_url, _user=data['token']),
            content_type='application/json')
        eq_(r.status_code, 204)


class TestFeedbackHandler(TestPotatoCaptcha, RestOAuth):

    def setUp(self):
        super(TestFeedbackHandler, self).setUp()
        self.url = reverse('account-feedback')
        self.user = UserProfile.objects.get(pk=2519)
        self.default_data = {
            'chromeless': 'no',
            'feedback': u'Hér€ is whàt I rælly think.',
            'platform': u'Desktøp',
            'from_url': '/feedback',
            'sprout': 'potato'
        }
        self.headers = {
            'HTTP_USER_AGENT': 'Fiiia-fox',
            'REMOTE_ADDR': '48.151.623.42'
        }

    def _call(self, anonymous=False, data=None):
        post_data = self.default_data.copy()
        client = self.anon if anonymous else self.client
        if data:
            post_data.update(data)
        res = client.post(self.url, data=json.dumps(post_data),
                          **self.headers)
        return res, json.loads(res.content)

    def _test_success(self, res, data):
        eq_(201, res.status_code)

        fields = self.default_data.copy()

        # PotatoCaptcha field shouldn't be present in returned data.
        del fields['sprout']
        ok_('sprout' not in data)

        # Rest of the fields should all be here.
        for name in fields.keys():
            eq_(fields[name], data[name])

        eq_(len(mail.outbox), 1)
        assert self.default_data['feedback'] in mail.outbox[0].body
        assert self.headers['REMOTE_ADDR'] in mail.outbox[0].body

    def test_send(self):
        res, data = self._call()
        self._test_success(res, data)
        eq_(unicode(self.user), data['user'])
        email = mail.outbox[0]
        eq_(email.from_email, self.user.email)
        assert self.user.username in email.body
        assert self.user.name in email.body
        assert unicode(self.user.pk) in email.body
        assert self.user.email in email.body

    def test_send_urlencode(self):
        self.headers['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'
        post_data = self.default_data.copy()
        res = self.client.post(self.url, data=urlencode(post_data),
                               **self.headers)
        data = json.loads(res.content)
        self._test_success(res, data)
        eq_(unicode(self.user), data['user'])
        eq_(mail.outbox[0].from_email, self.user.email)

    def test_send_without_platform(self):
        del self.default_data['platform']
        self.url += '?dev=platfoo'

        res, data = self._call()
        self._test_success(res, data)
        assert 'platfoo' in mail.outbox[0].body

    def test_send_anonymous(self):
        res, data = self._call(anonymous=True)
        self._test_success(res, data)
        assert not data['user']
        assert 'Anonymous' in mail.outbox[0].body
        eq_(settings.NOBODY_EMAIL, mail.outbox[0].from_email)

    def test_send_potato(self):
        tuber_res, tuber_data = self._call(data={'tuber': 'potat-toh'},
                                           anonymous=True)
        potato_res, potato_data = self._call(data={'sprout': 'potat-toh'},
                                             anonymous=True)
        self._test_bad_api_potato_data(tuber_res, tuber_data)
        self._test_bad_api_potato_data(potato_res, potato_data)

    def test_missing_optional_field(self):
        res, data = self._call(data={'platform': None})
        eq_(201, res.status_code)

    def test_send_bad_data(self):
        """
        One test to ensure that Feedback API is doing its validation duties.
        """
        res, data = self._call(data={'feedback': None})
        eq_(400, res.status_code)
        assert 'feedback' in data


class TestNewsletter(RestOAuth):
    VALID_EMAIL = 'bob@example.com'
    VALID_PLUS_EMAIL = 'bob+totally+real@example.com'
    INVALID_EMAIL = '!not_an_email'

    def setUp(self):
        super(TestNewsletter, self).setUp()
        self.url = reverse('account-newsletter')

    @patch('basket.subscribe')
    def test_signup_bad(self, subscribe):
        res = self.client.post(self.url,
                               data=json.dumps({'email': self.INVALID_EMAIL}))
        eq_(res.status_code, 400)
        ok_(not subscribe.called)

    @patch('basket.subscribe')
    def test_signup_empty(self, subscribe):
        res = self.client.post(self.url)
        eq_(res.status_code, 400)
        ok_(not subscribe.called)

    @patch('basket.subscribe')
    def test_signup_invalid_newsletter(self, subscribe):
        res = self.client.post(self.url, data={'email': self.VALID_EMAIL,
                                               'newsletter': 'invalid'})
        eq_(res.status_code, 400)
        ok_(not subscribe.called)

    @patch('basket.subscribe')
    def test_signup_anonymous(self, subscribe):
        res = self.anon.post(self.url,
                               data=json.dumps({'email': self.VALID_EMAIL}))
        eq_(res.status_code, 204)
        subscribe.assert_called_with(
            self.VALID_EMAIL, 'marketplace', lang='en-US',
            country='restofworld', trigger_welcome='Y', optin='Y', format='H')

    @patch('basket.subscribe')
    def test_signup(self, subscribe):
        res = self.client.post(self.url,
                               data=json.dumps({'email': self.VALID_EMAIL}))
        eq_(res.status_code, 204)
        subscribe.assert_called_with(
            self.VALID_EMAIL, 'marketplace', lang='en-US',
            country='restofworld', trigger_welcome='Y', optin='Y', format='H')

    @patch('basket.subscribe')
    def test_signup_plus(self, subscribe):
        res = self.client.post(
            self.url,
            data=json.dumps({'email': self.VALID_PLUS_EMAIL}))
        subscribe.assert_called_with(
            self.VALID_PLUS_EMAIL, 'marketplace', lang='en-US',
            country='restofworld', trigger_welcome='Y', optin='Y', format='H')
        eq_(res.status_code, 204)

    @patch('basket.subscribe')
    def test_signup_about_apps(self, subscribe):
        res = self.client.post(self.url,
                               data=json.dumps({'email': self.VALID_EMAIL,
                                                'newsletter': 'about:apps'}))
        eq_(res.status_code, 204)
        subscribe.assert_called_with(
            self.VALID_EMAIL, 'mozilla-and-you,marketplace-desktop',
            lang='en-US', country='restofworld', trigger_welcome='Y',
            optin='Y', format='H')


class TestAccountInfoView(RestOAuth):
    def setUp(self):
        super(TestAccountInfoView, self).setUp()
        # Make sure there is at least one FxA user.
        self.profile.update(source=amo.LOGIN_SOURCE_FXA)

    def url(self, email):
        return reverse('account-info', args=[email])

    def get(self, email):
        return self.client.get(self.url(email))

    def test_no_user(self):
        response = self.get('nope@noway.rich')
        eq_(response.status_code, 200)
        eq_(response.json['source'], 'unknown')

    def test_unknown_source_user(self):
        self.profile.update(source=amo.LOGIN_SOURCE_UNKNOWN)
        response = self.get(self.profile.email)
        eq_(response.status_code, 200)
        eq_(response.json['source'], 'unknown')

    def test_browser_id_source_user(self):
        self.profile.update(source=amo.LOGIN_SOURCE_BROWSERID)
        response = self.get(self.profile.email)
        eq_(response.status_code, 200)
        eq_(response.json['source'], 'unknown')

    def test_fxa_user(self):
        self.profile.update(source=amo.LOGIN_SOURCE_FXA)
        response = self.get(self.profile.email)
        eq_(response.status_code, 200)
        eq_(response.json['source'], 'firefox-accounts')


class TestPreverify(RestOAuth):
    @patch('mkt.account.utils.get_token_expiry', lambda td: 1400000000)
    def test_preverify(self):
        res = self.client.post(reverse('fxa-preverify'))
        eq_(res.status_code, 200)
        eq_(res.content, 'eyJhbGciOiJSUzI1NiIsImN0eSI6IkpXVCIsImprdSI6Ii9hcGkvdjEvYWNjb3VudC9meGEtcHJldmVyaWZ5LWtleS8ifQ.eyJ0eXAiOiJtb3ppbGxhL2Z4YS9wcmVWZXJpZnlUb2tlbi92MSIsImF1ZCI6Imh0dHBzOi8vc3RhYmxlLmRldi5sY2lwLm9yZy8iLCJleHAiOjE0MDAwMDAwMDAsInN1YiI6ImNmaW5rZUBtLmNvbSJ9.I9UlZEJAOwTIwYeqnyaiMVYOEf1-hsbHGR7zrOKEc89ntkUWPqvBfM2nTgEqHKQ9Lj3Pr2WDGspOeS6UE3eLKY0H3yNrA7AMbEMLsG2ZNwDOzdPe-6ctq8-WnFkrB9RxbtXxbNFCdMDkVblJRh91i7b5-t9752bN_k_e8FO1-Gg')

    def test_reject_unverified(self):
        self.user.is_verified = False
        self.user.save()
        res = self.client.post(reverse('fxa-preverify'))
        eq_(res.status_code, 403)

    def test_preverify_key(self):
        res = self.anon.get(reverse('fxa-preverify-key'))
        eq_(json.loads(res.content),
            {'keys': [{'e': 'AQAB', 'kty': 'RSA',
                       'n': '97209xSudEskAnd-6wYd3ED5MXve29bVxssOWRW5wHECX2MO0tzzfhOgdmD0e2X0Xgsv8vnFU0w0sWFjOtBJ1r2YAtnrcgpKiVVDcWm6EcOt-xS_CvZqwX8NZFktyxv-r9dpA9uRui0xQXy7JXS13rI0kq2VcWQuldiwYfDCPjM'
                   }]})
