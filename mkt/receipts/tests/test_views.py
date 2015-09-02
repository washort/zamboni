# -*- coding: utf-8 -*-
import calendar
import json
import time
import uuid

from django.conf import settings
from django.core.urlresolvers import reverse
from django.test.client import RequestFactory

import mock
from nose.tools import eq_, ok_

import mkt
from mkt.constants import apps
from mkt.developers.models import AppLog
from mkt.receipts.tests.test_models import TEST_LEEWAY
from mkt.receipts.utils import create_test_receipt
from mkt.site.fixtures import fixture
from mkt.site.helpers import absolutify
from mkt.site.tests import app_factory, MktPaths, TestCase
from mkt.users.models import UserProfile
from mkt.webapps.models import WebappUser, Webapp
from services.verify import settings as verify_settings
from services.verify import decode_receipt


class TestInstall(TestCase):
    fixtures = fixture('user_999', 'user_editor', 'user_editor_group',
                       'group_editor')

    def setUp(self):
        self.webapp = app_factory(manifest_url='http://cbc.ca/man')
        self.url = self.webapp.get_detail_url('record')
        self.user = UserProfile.objects.get(email='regular@mozilla.com')
        self.login(self.user.email)

    def test_pending_free_for_reviewer(self):
        self.webapp.update(status=mkt.STATUS_PENDING)
        self.login('editor@mozilla.com')
        eq_(self.client.post(self.url).status_code, 200)

    def test_pending_free_for_developer(self):
        WebappUser.objects.create(webapp=self.webapp, user=self.user)
        self.webapp.update(status=mkt.STATUS_PENDING)
        eq_(self.client.post(self.url).status_code, 200)

    def test_pending_free_for_anonymous(self):
        self.webapp.update(status=mkt.STATUS_PENDING)
        eq_(self.client.post(self.url).status_code, 404)

    def test_pending_paid_for_reviewer(self):
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        self.login('editor@mozilla.com')
        eq_(self.client.post(self.url).status_code, 200)
        # Because they aren't using reviewer tools, they'll get a normal
        # install record and receipt.
        eq_(self.webapp.installed.all()[0].install_type,
            apps.INSTALL_TYPE_USER)

    def test_pending_paid_for_admin(self):
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        self.grant_permission(self.user, '*:*')
        eq_(self.client.post(self.url).status_code, 200)
        # Check ownership ignores admin users.
        eq_(self.webapp.installed.all()[0].install_type,
            apps.INSTALL_TYPE_USER)

    def test_pending_paid_for_developer(self):
        WebappUser.objects.create(webapp=self.webapp, user=self.user)
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.client.post(self.url).status_code, 200)
        eq_(self.user.installed_set.all()[0].install_type,
            apps.INSTALL_TYPE_DEVELOPER)

    def test_pending_paid_for_anonymous(self):
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.client.post(self.url).status_code, 404)

    @mock.patch('mkt.webapps.models.Webapp.has_purchased')
    def test_paid(self, has_purchased):
        has_purchased.return_value = True
        self.webapp.update(premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.client.post(self.url).status_code, 200)

    def test_own_payments(self):
        self.webapp.update(premium_type=mkt.WEBAPP_OTHER_INAPP)
        eq_(self.client.post(self.url).status_code, 200)

    @mock.patch('mkt.webapps.models.Webapp.has_purchased')
    def test_not_paid(self, has_purchased):
        has_purchased.return_value = False
        self.webapp.update(premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.client.post(self.url).status_code, 403)

    def test_record_logged_out(self):
        self.client.logout()
        res = self.client.post(self.url)
        eq_(res.status_code, 200)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_log_metrics(self, cef):
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        logs = AppLog.objects.filter(webapp=self.webapp)
        eq_(logs.count(), 1)
        eq_(logs[0].activity_log.action, mkt.LOG.INSTALL_WEBAPP.id)

    @mock.patch('mkt.receipts.views.record_action')
    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_metrics(self, cef, record_action):
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(record_action.call_args[0][0], 'install')
        eq_(record_action.call_args[0][2], {'app-domain': u'http://cbc.ca',
                                            'app-id': self.webapp.pk,
                                            'anonymous': False})

    @mock.patch('mkt.receipts.views.record_action')
    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_metrics_packaged_app(self, cef, record_action):
        # Mimic packaged app.
        self.webapp.update(is_packaged=True, manifest_url=None,
                           app_domain='app://f.c')
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(record_action.call_args[0][0], 'install')
        eq_(record_action.call_args[0][2], {
            'app-domain': 'app://f.c',
            'app-id': self.webapp.pk,
            'anonymous': False})

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_cef_logs(self, cef):
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(len(cef.call_args_list), 1)
        eq_([x[0][2] for x in cef.call_args_list], ['sign'])

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_install(self, cef):
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        installed = self.user.installed_set.all()
        eq_(len(installed), 1)
        eq_(installed[0].install_type, apps.INSTALL_TYPE_USER)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_multiple_installs(self, cef):
        self.client.post(self.url)
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(self.user.installed_set.count(), 1)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_receipt(self, cef):
        res = self.client.post(self.url)
        content = json.loads(res.content)
        assert content.get('receipt'), content


class TestReceiptVerify(TestCase):
    fixtures = fixture('user_999', 'user_editor', 'user_editor_group',
                       'group_editor')

    def setUp(self):
        super(TestReceiptVerify, self).setUp()
        self.app = Webapp.objects.create(app_slug='foo', guid=uuid.uuid4())
        self.url = reverse('receipt.verify',
                           args=[self.app.guid])
        self.log = AppLog.objects.filter(webapp=self.app)
        self.reviewer = UserProfile.objects.get(pk=5497308)

    def get_mock(self, user=None, **kwargs):
        self.verify = mock.Mock()
        self.verify.return_value = json.dumps(kwargs)
        self.verify.check_without_purchase.return_value = json.dumps(
            {'status': 'ok'})
        self.verify.invalid.return_value = json.dumps({'status': 'invalid'})
        self.verify.user_id = user.pk if user else self.reviewer.pk
        return self.verify

    def test_post_required(self):
        eq_(self.client.get(self.url).status_code, 405)

    @mock.patch('mkt.receipts.views.Verify')
    def test_empty(self, verify):
        vfy = self.get_mock(user=self.reviewer, status='invalid')
        # Because the receipt was empty, this never got set and so
        # we didn't log it.
        vfy.user_id = None
        verify.return_value = vfy
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(self.log.count(), 0)
        eq_(json.loads(res.content)['status'], 'invalid')

    @mock.patch('mkt.receipts.views.Verify')
    def test_good(self, verify):
        verify.return_value = self.get_mock(user=self.reviewer, status='ok')
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(self.log.count(), 1)
        eq_(json.loads(res.content)['status'], 'ok')

    @mock.patch('mkt.receipts.views.Verify')
    def test_not_reviewer(self, verify):
        self.reviewer.groups.clear()
        verify.return_value = self.get_mock(user=self.reviewer, status='ok')
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(self.log.count(), 0)
        eq_(json.loads(res.content)['status'], 'invalid')

    @mock.patch('mkt.receipts.views.Verify')
    def test_not_there(self, verify):
        verify.return_value = self.get_mock(user=self.reviewer, status='ok')
        self.reviewer.delete()
        res = self.client.post(self.url)
        eq_(res['Access-Control-Allow-Origin'], '*')
        eq_(json.loads(res.content)['status'], 'invalid')

    @mock.patch('mkt.receipts.views.Verify')
    def test_logs(self, verify):
        verify.return_value = self.get_mock(user=self.reviewer, status='ok')
        eq_(self.log.count(), 0)
        res = self.client.post(self.url)
        eq_(self.log.count(), 1)
        eq_(res.status_code, 200)

    @mock.patch('mkt.receipts.views.Verify')
    def test_logs_developer(self, verify):
        developer = UserProfile.objects.get(pk=999)
        WebappUser.objects.create(webapp=self.app, user=developer)
        verify.return_value = self.get_mock(user=developer, status='ok')
        res = self.client.post(self.url)
        eq_(res['Access-Control-Allow-Origin'], '*')
        eq_(self.log.count(), 1)
        eq_(res.status_code, 200)


class TestReceiptIssue(TestCase):
    fixtures = fixture('user_999', 'user_editor', 'user_editor_group',
                       'group_editor', 'webapp_337141')

    def setUp(self):
        super(TestReceiptIssue, self).setUp()
        self.app = Webapp.objects.get(pk=337141)
        self.url = reverse('receipt.issue', args=[self.app.app_slug])
        self.reviewer = UserProfile.objects.get(pk=5497308)
        self.user = UserProfile.objects.get(pk=999)

    @mock.patch('mkt.receipts.views.create_receipt')
    def test_issued(self, create_receipt):
        create_receipt.return_value = 'foo'
        self.login(self.reviewer.email)
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(create_receipt.call_args[1]['flavour'], 'reviewer')
        eq_(self.reviewer.installed_set.all()[0].install_type,
            apps.INSTALL_TYPE_REVIEWER)

    def test_get(self):
        self.login(self.reviewer.email)
        res = self.client.get(self.url)
        eq_(res.status_code, 405)

    def test_issued_anon(self):
        res = self.client.post(self.url)
        eq_(res.status_code, 403)

    def test_issued_not_reviewer(self):
        self.login(self.user.email)
        res = self.client.post(self.url)
        eq_(res.status_code, 403)

    @mock.patch('mkt.receipts.views.create_receipt')
    def test_issued_developer(self, create_receipt):
        create_receipt.return_value = 'foo'
        WebappUser.objects.create(user=self.user, webapp=self.app)
        self.login(self.user.email)
        res = self.client.post(self.url)
        eq_(res.status_code, 200)
        eq_(create_receipt.call_args[1]['flavour'], 'developer')
        eq_(self.user.installed_set.all()[0].install_type,
            apps.INSTALL_TYPE_DEVELOPER)

    @mock.patch('mkt.receipts.views.create_receipt')
    def test_unicode_name(self, create_receipt):
        """
        Regression test to ensure that the CEF log works. Pass through the
        app.pk instead of the full unicode name, until the CEF library is
        fixed, or heka is used.
        """
        create_receipt.return_value = 'foo'
        self.app.name = u'\u0627\u0644\u062a\u0637\u0628-news'
        self.app.save()

        self.login(self.reviewer.email)
        res = self.client.post(self.url)
        eq_(res.status_code, 200)


class TestReceiptCheck(TestCase):
    fixtures = fixture('user_999', 'user_editor', 'user_editor_group',
                       'group_editor', 'webapp_337141')

    def setUp(self):
        super(TestReceiptCheck, self).setUp()
        self.app = Webapp.objects.get(pk=337141)
        self.app.update(status=mkt.STATUS_PENDING)
        self.url = reverse('receipt.check',
                           args=[self.app.guid])
        self.reviewer = UserProfile.objects.get(pk=5497308)
        self.user = UserProfile.objects.get(pk=999)

    def test_anon(self):
        eq_(self.client.get(self.url).status_code, 302)

    def test_not_reviewer(self):
        self.login(self.user.email)
        eq_(self.client.get(self.url).status_code, 403)

    def test_not_there(self):
        self.login(self.reviewer.email)
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        eq_(json.loads(res.content)['status'], False)

    def test_there(self):
        self.login(self.reviewer.email)
        mkt.log(mkt.LOG.RECEIPT_CHECKED, self.app, user=self.reviewer)
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        eq_(json.loads(res.content)['status'], True)


class RawRequestFactory(RequestFactory):
    """A request factory that does not encode the body."""

    def _encode_data(self, data, content_type):
        return data


@mock.patch.object(verify_settings, 'WEBAPPS_RECEIPT_KEY',
                   MktPaths.sample_key())
@mock.patch.object(settings, 'SITE_URL', 'https://foo.com')
@mock.patch.object(verify_settings, 'DOMAIN', 'foo.com')
class TestDevhubReceipts(TestCase):

    def setUp(self):
        self.issue = reverse('receipt.test.issue')

    def test_verify_supports_cors(self):
        res = self.client.options(reverse('receipt.test.verify',
                                          args=['ok']))
        eq_(res.status_code, 200, res)
        eq_(res['Access-Control-Allow-Headers'],
            'content-type, accept, x-fxpay-version')
        eq_(res['Access-Control-Allow-Origin'], '*')

    def test_install_page(self):
        eq_(self.client.get(reverse('receipt.test.install')).status_code, 200)

    def test_details_page(self):
        eq_(self.client.get(reverse('receipt.test.details')).status_code, 200)

    def test_issue_get(self):
        eq_(self.client.get(self.issue).status_code, 405)

    def test_issue_none(self):
        data = {'receipt_type': 'none', 'manifest_url': 'http://foo.com/'}
        res = self.client.post(self.issue, data=data)
        eq_(json.loads(res.content)['receipt'], '')

    def test_bad_url(self):
        data = {'receipt_type': 'none', 'manifest_url': ''}
        res = self.client.post(self.issue, data=data)
        ok_(json.loads(res.content)['error'], '')

    def test_issue_expired(self):
        data = {'receipt_type': 'expired', 'manifest_url': 'http://foo.com/'}
        res = self.client.post(self.issue, data=data)
        data = decode_receipt(json.loads(res.content)['receipt']
                                  .encode('ascii'))
        eq_(data['verify'], absolutify(reverse('receipt.test.verify',
                                       kwargs={'status': 'expired'})))
        ok_(data['exp'] > (calendar.timegm(time.gmtime()) +
                           (60 * 60 * 24) - TEST_LEEWAY))

    def test_issue_other(self):
        data = {'receipt_type': 'foo', 'manifest_url': ''}
        res = self.client.post(self.issue, data=data)
        ok_(json.loads(res.content)['error'])

    def test_verify_fails(self):
        res = self.client.post(reverse('receipt.test.verify',
                                       args=['expired']))
        eq_(json.loads(res.content)['status'], 'invalid')

    def test_verify(self):
        receipt = create_test_receipt('http://foo', 'expired')
        res = self.client.post(reverse('receipt.test.verify',
                                       args=['expired']),
                               receipt,
                               content_type='text/plain')
        eq_(json.loads(res.content)['status'], 'expired')
