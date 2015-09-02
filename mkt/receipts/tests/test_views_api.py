import json

from django.core.urlresolvers import reverse

import mock
from nose.tools import eq_, ok_
from receipts.receipts import Receipt

import mkt
from mkt.api.tests.test_oauth import RestOAuth
from mkt.constants import apps
from mkt.constants.payments import CONTRIB_NO_CHARGE
from mkt.developers.models import AppLog
from mkt.receipts.utils import create_receipt
from mkt.site.fixtures import fixture
from mkt.site.tests import TestCase
from mkt.users.models import UserProfile
from mkt.webapps.models import WebappUser, Webapp


class TestAPI(RestOAuth):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestAPI, self).setUp()
        self.webapp = Webapp.objects.get(pk=337141)
        self.url = reverse('receipt.install')
        self.data = json.dumps({'app': self.webapp.pk})
        self.profile = self.user

    def test_has_cors(self):
        self.assertCORS(self.client.post(self.url), 'post',
                        headers=['content-type', 'accept', 'x-fxpay-version'])

    def post(self, anon=False):
        client = self.client if not anon else self.anon
        return client.post(self.url, data=self.data)

    def test_no_app(self):
        self.data = json.dumps({'app': 0})
        eq_(self.post().status_code, 400)

    def test_app_slug(self):
        self.data = json.dumps({'app': self.webapp.app_slug})
        eq_(self.post().status_code, 201)

    def test_record_logged_out(self):
        res = self.post(anon=True)
        eq_(res.status_code, 403)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_cef_logs(self, cef):
        eq_(self.post().status_code, 201)
        eq_(len(cef.call_args_list), 1)
        eq_([x[0][2] for x in cef.call_args_list], ['sign'])

    @mock.patch('mkt.installs.utils.record_action')
    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_metrics(self, cef, record_action):
        res = self.post()
        eq_(res.status_code, 201)
        record_action.assert_called_with(
            'install', mock.ANY, {'app-domain': u'http://micropipes.com',
                                  'app-id': self.webapp.pk,
                                  'region': 'restofworld',
                                  'anonymous': False})

    @mock.patch('mkt.installs.utils.record_action')
    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_metrics_packaged_app(self, cef, record_action):
        # Mimic packaged app.
        self.webapp.update(is_packaged=True, manifest_url=None,
                           app_domain=None)
        res = self.post()
        eq_(res.status_code, 201)
        record_action.assert_called_with(
            'install', mock.ANY, {'app-domain': None, 'app-id': self.webapp.pk,
                                  'region': 'restofworld', 'anonymous': False})

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_log_metrics(self, cef):
        eq_(self.post().status_code, 201)
        logs = AppLog.objects.filter(webapp=self.webapp)
        eq_(logs.count(), 1)
        eq_(logs[0].activity_log.action, mkt.LOG.INSTALL_WEBAPP.id)


class TestDevhubAPI(RestOAuth):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestDevhubAPI, self).setUp()
        self.data = json.dumps({'manifest_url': 'http://foo.com',
                                'receipt_type': 'expired'})
        self.url = reverse('receipt.test')

    def test_has_cors(self):
        self.assertCORS(self.client.post(self.url), 'post',
                        headers=['content-type', 'accept', 'x-fxpay-version'])

    def test_decode(self):
        res = self.anon.post(self.url, data=self.data)
        eq_(res.status_code, 201)
        data = json.loads(res.content)
        receipt = Receipt(data['receipt'].encode('ascii')).receipt_decoded()
        eq_(receipt['typ'], u'test-receipt')

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_cef_log(self, cef):
        self.anon.post(self.url, data=self.data)
        cef.assert_called_with(mock.ANY, None, 'sign', 'Test receipt signing')


class TestReceipt(RestOAuth):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestReceipt, self).setUp()
        self.webapp = Webapp.objects.get(pk=337141)
        self.data = json.dumps({'app': self.webapp.pk})
        self.profile = UserProfile.objects.get(pk=2519)
        self.url = reverse('receipt.install')

    def post(self, anon=False):
        client = self.client if not anon else self.anon
        return client.post(self.url, data=self.data)

    def test_pending_free_for_developer(self):
        WebappUser.objects.create(webapp=self.webapp, user=self.profile)
        self.webapp.update(status=mkt.STATUS_PENDING)
        eq_(self.post().status_code, 201)

    def test_pending_free_for_anonymous(self):
        self.webapp.update(status=mkt.STATUS_PENDING)
        self.anon.post(self.url)
        eq_(self.post(anon=True).status_code, 403)

    def test_pending_paid_for_developer(self):
        WebappUser.objects.create(webapp=self.webapp, user=self.profile)
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.post().status_code, 201)
        eq_(self.profile.installed_set.all()[0].install_type,
            apps.INSTALL_TYPE_DEVELOPER)

    def test_pending_paid_for_anonymous(self):
        self.webapp.update(status=mkt.STATUS_PENDING,
                           premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.post(anon=True).status_code, 403)

    @mock.patch('mkt.webapps.models.Webapp.has_purchased')
    def test_paid(self, has_purchased):
        has_purchased.return_value = True
        self.webapp.update(premium_type=mkt.WEBAPP_PREMIUM)
        r = self.post()
        eq_(r.status_code, 201)

    def test_own_payments(self):
        self.webapp.update(premium_type=mkt.WEBAPP_OTHER_INAPP)
        eq_(self.post().status_code, 201)

    def test_no_charge(self):
        self.make_premium(self.webapp, '0.00')
        eq_(self.post().status_code, 201)
        eq_(self.profile.installed_set.all()[0].install_type,
            apps.INSTALL_TYPE_USER)
        eq_(self.profile.webapppurchase_set.all()[0].type,
            CONTRIB_NO_CHARGE)

    @mock.patch('mkt.webapps.models.Webapp.has_purchased')
    def test_not_paid(self, has_purchased):
        has_purchased.return_value = False
        self.webapp.update(premium_type=mkt.WEBAPP_PREMIUM)
        eq_(self.post().status_code, 402)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_install(self, cef):
        self.post()
        installed = self.profile.installed_set.all()
        eq_(len(installed), 1)
        eq_(installed[0].install_type, apps.INSTALL_TYPE_USER)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_multiple_installs(self, cef):
        self.post()
        r = self.post()
        eq_(r.status_code, 201)
        eq_(self.profile.installed_set.count(), 1)

    @mock.patch('mkt.receipts.views.receipt_cef.log')
    def test_record_receipt(self, cef):
        r = self.post()
        ok_(Receipt(r.data['receipt']).receipt_decoded())


class TestReissue(TestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        self.user = UserProfile.objects.get(pk=2519)
        self.webapp = Webapp.objects.get(pk=337141)
        self.url = reverse('receipt.reissue')
        self.data = json.dumps({'app': self.webapp.pk})

        verify = mock.patch('mkt.receipts.views.Verify.check_full')
        self.verify = verify.start()
        self.addCleanup(verify.stop)

    def test_get(self):
        eq_(self.client.get(self.url).status_code, 405)

    def test_invalid(self):
        self.verify.return_value = {'status': 'invalid'}
        res = self.client.post(self.url, data={})
        eq_(res.status_code, 400)

    def test_valid(self):
        self.verify.return_value = {'status': 'valid'}
        res = self.client.post(self.url, data={})
        eq_(res.status_code, 400)
        data = json.loads(res.content)
        ok_(not data['receipt'])
        ok_(data['status'], 'valid')

    def test_expired(self):
        receipt = create_receipt(self.webapp, self.user, 'some-uuid')
        self.verify.return_value = {'status': 'expired'}
        res = self.client.post(self.url, data=receipt,
                               content_type='text/plain')
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        ok_(data['receipt'])
        eq_(data['status'], 'expired')
