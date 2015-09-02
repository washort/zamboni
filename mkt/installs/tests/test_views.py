import json

from django.core.urlresolvers import reverse

from mock import ANY, patch
from nose.tools import eq_

import mkt
from mkt.api.tests.test_oauth import RestOAuth
from mkt.constants.apps import INSTALL_TYPE_DEVELOPER, INSTALL_TYPE_USER
from mkt.site.fixtures import fixture
from mkt.webapps.models import WebappUser, Installed, Webapp


class TestAPI(RestOAuth):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestAPI, self).setUp()
        self.webapp = Webapp.objects.get(pk=337141)
        self.url = reverse('app-install-list')
        self.data = json.dumps({'app': self.webapp.pk})
        self.profile = self.user

    def test_has_cors(self):
        self.assertCORS(self.client.post(self.url), 'post')

    def post(self, anon=False):
        client = self.anon if anon else self.client
        return client.post(self.url, data=self.data)

    def test_no_app(self):
        self.data = json.dumps({'app': 0})
        eq_(self.post().status_code, 400)

    def test_not_public(self):
        self.webapp.update(status=mkt.STATUS_DISABLED)
        self.data = json.dumps({'app': self.webapp.app_slug})
        eq_(self.post().status_code, 403)

    def test_not_paid(self):
        self.webapp.update(premium_type=mkt.WEBAPP_PREMIUM)
        self.data = json.dumps({'app': self.webapp.app_slug})
        eq_(self.post().status_code, 400)

    def test_app_slug(self):
        self.data = json.dumps({'app': self.webapp.app_slug})
        eq_(self.post().status_code, 201)
        eq_(self.profile.reload().installed_set.all()[0].webapp, self.webapp)

    def test_app_pk(self):
        self.data = json.dumps({'app': self.webapp.pk})
        eq_(self.post().status_code, 201)
        eq_(self.profile.reload().installed_set.all()[0].webapp, self.webapp)

    @patch('mkt.installs.utils.record_action')
    def test_logged(self, record_action):
        self.data = json.dumps({'app': self.webapp.pk})
        eq_(self.post().status_code, 201)
        record_action.assert_called_with(
            'install', ANY,
            {'app-domain': u'http://micropipes.com', 'app-id': 337141L,
             'region': 'restofworld', 'anonymous': False})

    @patch('mkt.installs.utils.record_action')
    def test_logged_anon(self, record_action):
        self.data = json.dumps({'app': self.webapp.pk})
        eq_(self.post(anon=True).status_code, 201)
        record_action.assert_called_with(
            'install', ANY,
            {'app-domain': u'http://micropipes.com', 'app-id': 337141L,
             'region': 'restofworld', 'anonymous': True})

    @patch('mkt.installs.utils.record_action')
    def test_app_install_twice(self, record_action):
        Installed.objects.create(user=self.profile, webapp=self.webapp,
                                 install_type=INSTALL_TYPE_USER)
        eq_(self.post().status_code, 202)

    def test_app_install_developer(self):
        WebappUser.objects.create(webapp=self.webapp, user=self.profile)
        self.data = json.dumps({'app': self.webapp.app_slug})
        eq_(self.post().status_code, 201)
        eq_(self.profile.reload().installed_set.all()[0].install_type,
            INSTALL_TYPE_DEVELOPER)

    def test_app_install_developer_not_public(self):
        self.webapp.update(status=mkt.STATUS_DISABLED)
        self.test_app_install_developer()
