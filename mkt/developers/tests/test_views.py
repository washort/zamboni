# -*- coding: utf-8 -*-
import datetime
import json
import os
import tempfile
from contextlib import contextmanager

from django.conf import settings
from django.core.files.storage import default_storage as storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.client import RequestFactory
from django.test.utils import override_settings

import mock
from nose.plugins.attrib import attr
from nose.tools import eq_, ok_
from pyquery import PyQuery as pq

import amo
import amo.tests
from addons.models import Addon, AddonDeviceType, AddonUpsell, AddonUser
from amo.helpers import absolutify
from amo.tests import (app_factory, assert_no_validation_errors,
                       version_factory)
from amo.tests.test_helpers import get_image_path
from amo.urlresolvers import reverse
from amo.utils import urlparams
from browse.tests import test_default_sort, test_listing_sort
from files.models import File, FileUpload
from files.tests.test_models import UploadTest as BaseUploadTest
from market.models import AddonPremium, Price
from stats.models import Contribution
from translations.models import Translation
from users.models import UserProfile
from versions.models import Version

from lib.iarc.utils import get_iarc_app_title

import mkt
from mkt.constants import MAX_PACKAGED_APP_SIZE
from mkt.developers import tasks
from mkt.developers.views import (_filter_transactions, _get_transactions,
                                  _ratings_success_msg, _submission_msgs,
                                  content_ratings, content_ratings_edit)
from mkt.site.fixtures import fixture
from mkt.submit.models import AppSubmissionChecklist
from mkt.webapps.models import ContentRating, Webapp


class AppHubTest(amo.tests.TestCase):
    fixtures = fixture('prices', 'webapp_337141') + ['base/users']

    def setUp(self):
        self.create_flag('allow-b2g-paid-submission')

        self.url = reverse('mkt.developers.apps')
        self.user = UserProfile.objects.get(username='31337')
        assert self.client.login(username=self.user.email, password='password')

    def clone_addon(self, num, addon_id=337141):
        ids = []
        for i in xrange(num):
            addon = Addon.objects.get(id=addon_id)
            new_addon = Addon.objects.create(type=addon.type,
                status=addon.status, name='cloned-addon-%s-%s' % (addon_id, i))
            AddonUser.objects.create(user=self.user, addon=new_addon)
            ids.append(new_addon.id)
        return ids

    def get_app(self):
        return Addon.objects.get(id=337141)


class TestHome(amo.tests.TestCase):
    fixtures = ['base/users']

    def setUp(self):
        self.url = reverse('mkt.developers.apps')

    def test_legacy_login_redirect(self):
        r = self.client.get('/users/login')
        got, exp = r['Location'], '/login'
        assert got.endswith(exp), 'Expected %s. Got %s.' % (exp, got)

    def test_login_redirect(self):
        r = self.client.get(self.url)
        self.assertLoginRedirects(r, '/developers/submissions', 302)

    def test_home_anonymous(self):
        r = self.client.get(self.url, follow=True)
        eq_(r.status_code, 200)
        self.assertTemplateUsed(r, 'developers/login.html')

    def test_home_authenticated(self):
        assert self.client.login(username='regular@mozilla.com',
                                 password='password')
        r = self.client.get(self.url, follow=True)
        eq_(r.status_code, 200)
        self.assertTemplateUsed(r, 'developers/apps/dashboard.html')


class TestAppBreadcrumbs(AppHubTest):

    def setUp(self):
        super(TestAppBreadcrumbs, self).setUp()

    def test_regular_breadcrumbs(self):
        r = self.client.get(reverse('submit.app'), follow=True)
        eq_(r.status_code, 200)
        expected = [
            ('Home', reverse('home')),
            ('Developers', reverse('ecosystem.landing')),
            ('Submit App', None),
        ]
        amo.tests.check_links(expected, pq(r.content)('#breadcrumbs li'))

    def test_webapp_management_breadcrumbs(self):
        webapp = Webapp.objects.get(id=337141)
        AddonUser.objects.create(user=self.user, addon=webapp)
        r = self.client.get(webapp.get_dev_url('edit'))
        eq_(r.status_code, 200)
        expected = [
            ('Home', reverse('home')),
            ('Developers', reverse('ecosystem.landing')),
            ('My Submissions', reverse('mkt.developers.apps')),
            (unicode(webapp.name), None),
        ]
        amo.tests.check_links(expected, pq(r.content)('#breadcrumbs li'))


class TestAppDashboard(AppHubTest):

    def test_no_apps(self):
        Addon.objects.all().delete()
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        eq_(pq(r.content)('#dashboard .item').length, 0)

    def make_mine(self):
        AddonUser.objects.create(addon_id=337141, user=self.user)

    def test_public_app(self):
        app = self.get_app()
        self.make_mine()
        doc = pq(self.client.get(self.url).content)
        item = doc('.item[data-addonid=%s]' % app.id)
        assert item.find('.price'), 'Expected price'
        assert item.find('.item-details'), 'Expected item details'
        assert not item.find('p.incomplete'), (
            'Unexpected message about incomplete add-on')
        eq_(doc('.status-link').length, 1)
        eq_(doc('.more-actions-popup').length, 0)

    def test_incomplete_app(self):
        app = self.get_app()
        app.update(status=amo.STATUS_NULL)
        self.make_mine()
        doc = pq(self.client.get(self.url).content)
        assert doc('.item[data-addonid=%s] p.incomplete' % app.id), (
            'Expected message about incompleted add-on')
        eq_(doc('.more-actions-popup').length, 0)

    def test_packaged_version(self):
        app = self.get_app()
        version = Version.objects.create(addon=app, version='1.23')
        app.update(_current_version=version, is_packaged=True)
        self.make_mine()
        doc = pq(self.client.get(self.url).content)
        eq_(doc('.item[data-addonid=%s] .item-current-version' % app.id
                ).text(),
            'Packaged App Version: 1.23')

    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    def test_pending_version(self, ucm):
        ucm.return_value = True

        app = self.get_app()
        self.make_mine()
        app.update(is_packaged=True)
        Version.objects.create(addon=app, version='1.24')
        doc = pq(self.client.get(self.url).content)
        eq_(doc('.item[data-addonid=%s] .item-latest-version' % app.id
                ).text(),
            'Pending Version: 1.24')

    def test_action_links(self):
        self.create_switch('iarc')
        self.create_switch('comm-dashboard')
        self.create_switch('view-transactions')
        app = self.get_app()
        app.update(public_stats=True, is_packaged=True,
                   premium_type=amo.ADDON_PREMIUM_INAPP)
        self.make_mine()
        doc = pq(self.client.get(self.url).content)
        expected = [
            ('Edit Listing', app.get_dev_url()),
            ('Add New Version', app.get_dev_url('versions')),
            ('Status & Versions', app.get_dev_url('versions')),
            ('Content Ratings', app.get_dev_url('ratings')),
            ('Compatibility & Payments', app.get_dev_url('payments')),
            ('In-App Payments', app.get_dev_url('in_app_config')),
            ('Team Members', app.get_dev_url('owner')),
            ('View Listing', app.get_url_path()),

            ('Messages', app.get_comm_thread_url()),
            ('Statistics', app.get_stats_url()),
            ('Transactions', urlparams(
                reverse('mkt.developers.transactions'), app=app.id)),
        ]
        amo.tests.check_links(expected, doc('a.action-link'), verify=False)


class TestAppDashboardSorting(AppHubTest):

    def setUp(self):
        super(TestAppDashboardSorting, self).setUp()
        self.my_apps = self.user.addons
        self.url = reverse('mkt.developers.apps')
        self.clone(3)

    def clone(self, num=3):
        for x in xrange(num):
            app = amo.tests.addon_factory(type=amo.ADDON_WEBAPP)
            AddonUser.objects.create(addon=app, user=self.user)

    def test_pagination(self):
        doc = pq(self.client.get(self.url).content)('#dashboard')
        eq_(doc('.item').length, 4)
        eq_(doc('#sorter').length, 1)
        eq_(doc('.paginator').length, 0)

        self.clone(7)  # 4 + 7 = 11 (paginator appears for 11+ results)
        doc = pq(self.client.get(self.url).content)('#dashboard')
        eq_(doc('.item').length, 10)
        eq_(doc('#sorter').length, 1)
        eq_(doc('.paginator').length, 1)

        doc = pq(self.client.get(self.url, dict(page=2)).content)('#dashboard')
        eq_(doc('.item').length, 1)
        eq_(doc('#sorter').length, 1)
        eq_(doc('.paginator').length, 1)

    def test_default_sort(self):
        test_default_sort(self, 'name', 'name', reverse=False)

    def test_newest_sort(self):
        test_listing_sort(self, 'created', 'created')


class TestDevRequired(AppHubTest):

    def setUp(self):
        self.webapp = Addon.objects.get(id=337141)
        self.get_url = self.webapp.get_dev_url('payments')
        self.post_url = self.webapp.get_dev_url('payments.disable')
        self.user = UserProfile.objects.get(username='31337')
        assert self.client.login(username=self.user.email, password='password')
        self.au = AddonUser.objects.get(user=self.user, addon=self.webapp)
        eq_(self.au.role, amo.AUTHOR_ROLE_OWNER)
        self.make_price()

    def test_anon(self):
        self.client.logout()
        r = self.client.get(self.get_url, follow=True)
        login = reverse('users.login')
        self.assertRedirects(r, '%s?to=%s' % (login, self.get_url))

    def test_dev_get(self):
        eq_(self.client.get(self.get_url).status_code, 200)

    def test_dev_post(self):
        self.assertRedirects(self.client.post(self.post_url), self.get_url)

    def test_viewer_get(self):
        self.au.role = amo.AUTHOR_ROLE_VIEWER
        self.au.save()
        eq_(self.client.get(self.get_url).status_code, 200)

    def test_viewer_post(self):
        self.au.role = amo.AUTHOR_ROLE_VIEWER
        self.au.save()
        eq_(self.client.post(self.get_url).status_code, 403)

    def test_disabled_post_dev(self):
        self.webapp.update(status=amo.STATUS_DISABLED)
        eq_(self.client.post(self.get_url).status_code, 403)

    def test_disabled_post_admin(self):
        self.webapp.update(status=amo.STATUS_DISABLED)
        assert self.client.login(username='admin@mozilla.com',
                                 password='password')
        self.assertRedirects(self.client.post(self.post_url), self.get_url)


@mock.patch('mkt.developers.forms_payments.PremiumForm.clean',
            new=lambda x: x.cleaned_data)
class TestMarketplace(amo.tests.TestCase):
    fixtures = fixture('prices', 'webapp_337141')

    def setUp(self):
        self.create_flag('allow-b2g-paid-submission')

        self.addon = Addon.objects.get(id=337141)
        self.addon.update(status=amo.STATUS_NOMINATED,
                          highest_status=amo.STATUS_NOMINATED)

        self.url = self.addon.get_dev_url('payments')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def get_price_regions(self, price):
        return sorted(set([p['region'] for p in price.prices() if p['paid']]))

    def setup_premium(self):
        self.price = Price.objects.get(pk=1)
        self.price_two = Price.objects.get(pk=3)
        self.other_addon = Addon.objects.create(type=amo.ADDON_WEBAPP,
                                                premium_type=amo.ADDON_FREE)
        self.other_addon.update(status=amo.STATUS_PUBLIC)
        AddonUser.objects.create(addon=self.other_addon,
                                 user=self.addon.authors.all()[0])
        AddonPremium.objects.create(addon=self.addon, price_id=self.price.pk)
        self.addon.update(premium_type=amo.ADDON_PREMIUM)
        self.paid_regions = self.get_price_regions(self.price)
        self.paid_regions_two = self.get_price_regions(self.price_two)

    def get_data(self, **kw):
        data = {
            'price': self.price.pk,
            'upsell_of': self.other_addon.pk,
            'regions': mkt.regions.REGION_IDS,
        }
        data.update(kw)
        return data

    def test_initial_free(self):
        AddonDeviceType.objects.create(
            addon=self.addon, device_type=amo.DEVICE_GAIA.id)
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        assert 'Change to Paid' in res.content

    def test_initial_paid(self):
        self.setup_premium()
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        eq_(res.context['form'].initial['price'], self.price.pk)
        assert 'Change to Free' in res.content

    def test_set(self):
        self.setup_premium()
        res = self.client.post(
            self.url, data=self.get_data(price=self.price_two.pk,
                                         regions=self.paid_regions_two))
        eq_(res.status_code, 302)
        self.addon = Addon.objects.get(pk=self.addon.pk)
        eq_(self.addon.addonpremium.price, self.price_two)

    def test_set_upsell(self):
        self.setup_premium()
        res = self.client.post(self.url,
            data=self.get_data(regions=self.paid_regions))
        eq_(res.status_code, 302)
        eq_(len(self.addon._upsell_to.all()), 1)

    def test_remove_upsell(self):
        self.setup_premium()
        upsell = AddonUpsell.objects.create(
            free=self.other_addon, premium=self.addon)
        eq_(self.addon._upsell_to.all()[0], upsell)
        self.client.post(self.url,
            data=self.get_data(upsell_of='', regions=self.paid_regions))
        eq_(len(self.addon._upsell_to.all()), 0)

    def test_replace_upsell(self):
        self.setup_premium()
        # Make this add-on an upsell of some free add-on.
        upsell = AddonUpsell.objects.create(free=self.other_addon,
                                            premium=self.addon)
        # And this will become our new upsell, replacing the one above.
        new = Addon.objects.create(type=amo.ADDON_WEBAPP,
                                   premium_type=amo.ADDON_FREE,
                                   status=amo.STATUS_PUBLIC)
        AddonUser.objects.create(addon=new, user=self.addon.authors.all()[0])

        eq_(self.addon._upsell_to.all()[0], upsell)
        self.client.post(self.url, self.get_data(upsell_of=new.id,
                                                 regions=self.paid_regions))
        upsell = self.addon._upsell_to.all()
        eq_(len(upsell), 1)
        eq_(upsell[0].free, new)


class TestPublicise(amo.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        self.create_switch('iarc')
        self.webapp = self.get_webapp()
        self.webapp.update(status=amo.STATUS_PUBLIC_WAITING)
        self.file = self.webapp.versions.latest().all_files[0]
        self.file.update(status=amo.STATUS_PUBLIC_WAITING)
        self.publicise_url = self.webapp.get_dev_url('publicise')
        self.status_url = self.webapp.get_dev_url('versions')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def get_webapp(self):
        return Addon.objects.no_cache().get(id=337141)

    def test_logout(self):
        self.client.logout()
        res = self.client.post(self.publicise_url)
        eq_(res.status_code, 302)
        eq_(self.get_webapp().status, amo.STATUS_PUBLIC_WAITING)

    def test_publicise_get(self):
        eq_(self.client.get(self.publicise_url).status_code, 405)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.tasks.index_webapps')
    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    @mock.patch('mkt.webapps.models.Webapp.update_supported_locales')
    @mock.patch('mkt.webapps.models.Webapp.update_name_from_package_manifest')
    def test_publicise(self, update_name, update_locales,
                       update_cached_manifests, index_webapps,
                       storefront_mock):
        index_webapps.delay.reset_mock()
        eq_(update_name.call_count, 0)
        eq_(update_locales.call_count, 0)
        eq_(update_cached_manifests.delay.call_count, 0)
        eq_(storefront_mock.call_count, 0)
        eq_(self.get_webapp().status, amo.STATUS_PUBLIC_WAITING)

        res = self.client.post(self.publicise_url)
        eq_(res.status_code, 302)
        eq_(self.get_webapp().status, amo.STATUS_PUBLIC)
        eq_(self.get_webapp().versions.latest().all_files[0].status,
            amo.STATUS_PUBLIC)

        eq_(update_name.call_count, 1)
        eq_(update_locales.call_count, 1)
        eq_(index_webapps.delay.call_count, 1)

        # App is not packaged, no need to call update_cached_manifests.
        eq_(update_cached_manifests.delay.call_count, 0)
        eq_(storefront_mock.call_count, 1)

    def test_status(self):
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#version-status form').attr('action'), self.publicise_url)
        # TODO: fix this when jenkins can get the jinja helpers loaded in
        # the correct order.
        #eq_(len(doc('strong.status-waiting')), 1)


class TestPubliciseVersion(amo.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        Addon.objects.filter(pk=337141).update(is_packaged=True)
        self.app = self.get_webapp()
        self.url = self.app.get_dev_url('versions.publicise')
        self.status_url = self.app.get_dev_url('versions')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def get_webapp(self):
        return Addon.objects.no_cache().get(pk=337141)

    def get_version_status(self):
        v = Version.objects.no_cache().get(pk=self.app.latest_version.pk)
        return v.all_files[0].status

    def post(self, pk=None):
        if not pk:
            pk = self.app.latest_version.pk
        return self.client.post(self.url, data={
            'version_id': pk
        })

    def test_logout(self):
        File.objects.filter(version__addon=self.app).update(
            status=amo.STATUS_PUBLIC_WAITING)
        self.client.logout()
        res = self.post()
        eq_(res.status_code, 302)
        eq_(self.get_version_status(), amo.STATUS_PUBLIC_WAITING)

    def test_publicise_get(self):
        eq_(self.client.get(self.url).status_code, 405)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    @mock.patch('mkt.webapps.models.Webapp.update_supported_locales')
    @mock.patch('mkt.webapps.models.Webapp.update_name_from_package_manifest')
    def test_publicise_version_new_waiting(self, update_name, update_locales,
                                           update_cached_manifests,
                                           index_webapps):
        """ Test publishing the latest, public_waiting version when the app is
        already public, with a current version also already public """
        eq_(self.app.status, amo.STATUS_PUBLIC)
        ver = version_factory(addon=self.app, version='2.0',
                              file_kw=dict(status=amo.STATUS_PUBLIC_WAITING))
        eq_(self.app.latest_version, ver)
        ok_(self.app.current_version != ver)

        index_webapps.delay.reset_mock()
        eq_(update_name.call_count, 0)
        eq_(update_locales.call_count, 0)
        eq_(update_cached_manifests.delay.call_count, 0)

        res = self.post()
        eq_(res.status_code, 302)
        eq_(self.get_version_status(), amo.STATUS_PUBLIC)
        eq_(self.get_webapp().current_version, ver)

        eq_(update_name.call_count, 1)
        eq_(update_locales.call_count, 1)
        eq_(update_cached_manifests.delay.call_count, 1)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    @mock.patch('mkt.webapps.models.Webapp.update_supported_locales')
    @mock.patch('mkt.webapps.models.Webapp.update_name_from_package_manifest')
    def test_publicise_version_cur_waiting_app_public(self, update_name,
                                                      update_locales,
                                                      update_cached_manifests,
                                                      index_webapps):
        """ Test publishing when the app is in a weird state: public but with
        only one version, which is public_waiting """
        File.objects.filter(version__addon=self.app).update(
            status=amo.STATUS_PUBLIC_WAITING)
        eq_(self.app.current_version, self.app.latest_version)
        eq_(self.app.status, amo.STATUS_PUBLIC)

        index_webapps.delay.reset_mock()
        eq_(update_name.call_count, 0)
        eq_(update_locales.call_count, 0)
        eq_(update_cached_manifests.delay.call_count, 0)

        res = self.post()
        eq_(res.status_code, 302)
        eq_(self.app.current_version, self.app.latest_version)
        eq_(self.get_version_status(), amo.STATUS_PUBLIC)
        eq_(self.app.reload().status, amo.STATUS_PUBLIC)

        eq_(update_name.call_count, 1)
        eq_(update_locales.call_count, 1)
        # only one version, update_version() won't change it, the mini-manifest
        # doesn't need to be updated.
        eq_(update_cached_manifests.delay.call_count, 0)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    @mock.patch('mkt.webapps.models.Webapp.update_supported_locales')
    @mock.patch('mkt.webapps.models.Webapp.update_name_from_package_manifest')
    def test_publicise_version_cur_waiting(self, update_name, update_locales,
                                           update_cached_manifests,
                                           index_webapps):
        """ Test publishing when the only version of the app is waiting """
        self.app.update(status=amo.STATUS_PUBLIC_WAITING)
        File.objects.filter(version__addon=self.app).update(
            status=amo.STATUS_PUBLIC_WAITING)
        eq_(self.app.current_version, self.app.latest_version)

        index_webapps.delay.reset_mock()
        eq_(update_name.call_count, 0)
        eq_(update_locales.call_count, 0)
        eq_(update_cached_manifests.delay.call_count, 0)

        res = self.post()
        eq_(res.status_code, 302)
        eq_(self.app.current_version, self.app.latest_version)
        eq_(self.get_version_status(), amo.STATUS_PUBLIC)
        eq_(self.app.reload().status, amo.STATUS_PUBLIC)

        eq_(update_name.call_count, 1)
        eq_(update_locales.call_count, 1)
        # only one version, update_version() won't change it, the mini-manifest
        # doesn't need to be updated.
        eq_(update_cached_manifests.delay.call_count, 0)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    @mock.patch('mkt.webapps.tasks.update_cached_manifests')
    @mock.patch('mkt.webapps.models.Webapp.update_supported_locales')
    @mock.patch('mkt.webapps.models.Webapp.update_name_from_package_manifest')
    def test_publicise_version_pending(self, update_name, update_locales,
                                       update_cached_manifests, index_webapps):
        """ Test publishing a pending version """
        version_factory(addon=self.app, version='2.0',
                        file_kw=dict(status=amo.STATUS_PENDING))
        self.app.reload()
        res = self.post()
        eq_(res.status_code, 302)
        eq_(self.get_version_status(), amo.STATUS_PENDING)
        assert not update_name.called
        assert not update_locales.called

    def test_status(self):
        File.objects.filter(version__addon=self.app).update(
            status=amo.STATUS_PUBLIC_WAITING)
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#version-list form').attr('action'), self.url)


class TestStatus(amo.tests.TestCase):
    fixtures = ['base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.webapp = Addon.objects.get(id=337141)
        self.file = self.webapp.versions.latest().all_files[0]
        self.file.update(status=amo.STATUS_DISABLED)
        self.status_url = self.webapp.get_dev_url('versions')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def test_status_when_packaged_public_dev(self):
        self.webapp.update(is_packaged=True)
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#disable-addon').length, 1)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#blocklist-app').length, 0)

    def test_status_when_packaged_public_admin(self):
        assert self.client.login(username='admin@mozilla.com',
                                 password='password')
        self.webapp.update(is_packaged=True)
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#disable-addon').length, 1)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#blocklist-app').length, 1)

    def test_status_when_packaged_rejected_dev(self):
        self.webapp.update(is_packaged=True, status=amo.STATUS_REJECTED)
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#disable-addon').length, 1)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#blocklist-app').length, 0)

    def test_status_when_packaged_rejected_admin(self):
        assert self.client.login(username='admin@mozilla.com',
                                 password='password')
        self.webapp.update(is_packaged=True, status=amo.STATUS_REJECTED)
        res = self.client.get(self.status_url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('#disable-addon').length, 1)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#blocklist-app').length, 0)


class TestDelete(amo.tests.TestCase):
    fixtures = ['webapps/337141-steamcube']

    def setUp(self):
        self.webapp = self.get_webapp()
        self.url = self.webapp.get_dev_url('delete')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def get_webapp(self):
        return Addon.objects.no_cache().get(id=337141)

    def test_post(self):
        r = self.client.post(self.url, follow=True)
        eq_(pq(r.content)('.notification-box').text(), 'App deleted.')
        self.assertRaises(Addon.DoesNotExist, self.get_webapp)


class TestResumeStep(amo.tests.TestCase):
    fixtures = ['base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.webapp = self.get_addon()
        self.url = reverse('submit.app.resume', args=[self.webapp.app_slug])
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def get_addon(self):
        return Addon.objects.no_cache().get(pk=337141)

    def test_no_step_redirect(self):
        r = self.client.get(self.url, follow=True)
        self.assertRedirects(r, self.webapp.get_dev_url('edit'), 302)

    def test_step_redirects(self):
        AppSubmissionChecklist.objects.create(addon=self.webapp,
                                              terms=True, manifest=True)
        r = self.client.get(self.url, follow=True)
        self.assert3xx(r, reverse('submit.app.details',
                                  args=[self.webapp.app_slug]))

    def test_no_resume_when_done(self):
        AppSubmissionChecklist.objects.create(addon=self.webapp,
                                              terms=True, manifest=True,
                                              details=True)
        r = self.client.get(self.webapp.get_dev_url('edit'), follow=True)
        eq_(r.status_code, 200)

    def test_resume_without_checklist(self):
        r = self.client.get(reverse('submit.app.details',
                                    args=[self.webapp.app_slug]))
        eq_(r.status_code, 200)


class TestUpload(BaseUploadTest):
    fixtures = ['base/apps', 'base/users']

    def setUp(self):
        super(TestUpload, self).setUp()
        assert self.client.login(username='regular@mozilla.com',
                                 password='password')
        self.package = self.packaged_app_path('mozball.zip')
        self.url = reverse('mkt.developers.upload')

    def post(self):
        # Has to be a binary, non xpi file.
        data = open(self.package, 'rb')
        return self.client.post(self.url, {'upload': data})

    def test_login_required(self):
        self.client.logout()
        r = self.post()
        eq_(r.status_code, 302)

    def test_create_fileupload(self):
        self.post()
        upload = FileUpload.objects.get(name='mozball.zip')
        eq_(upload.name, 'mozball.zip')
        data = open(self.package, 'rb').read()
        eq_(storage.open(upload.path).read(), data)

    def test_fileupload_user(self):
        self.client.login(username='regular@mozilla.com', password='password')
        self.post()
        user = UserProfile.objects.get(email='regular@mozilla.com')
        eq_(FileUpload.objects.get().user, user)

    def test_fileupload_ascii_post(self):
        path = u'apps/files/fixtures/files/jetpack.xpi'
        data = storage.open(os.path.join(settings.ROOT, path))
        replaced = path.replace('e', u'é')
        r = self.client.post(self.url, {'upload':
                                        SimpleUploadedFile(replaced,
                                                           data.read())})
        # If this is broke, we'll get a traceback.
        eq_(r.status_code, 302)

    @mock.patch('mkt.constants.MAX_PACKAGED_APP_SIZE', 1024)
    @mock.patch('mkt.developers.tasks.validator')
    def test_fileupload_too_big(self, validator):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            name = tf.name
            tf.write('x' * (MAX_PACKAGED_APP_SIZE + 1))

        with open(name) as tf:
            r = self.client.post(self.url, {'upload': tf})

        os.unlink(name)

        assert not validator.called, 'Validator erroneously invoked'

        # Test that we get back a validation failure for the upload.
        upload = FileUpload.objects.get()
        r = self.client.get(reverse('mkt.developers.upload_detail',
                                    args=[upload.uuid, 'json']))

        eq_(r.status_code, 200)
        data = json.loads(r.content)
        assert 'validation' in data, data
        assert 'success' in data['validation'], data
        assert not data['validation']['success'], data['validation']

    @attr('validator')
    def test_fileupload_validation(self):
        self.post()
        fu = FileUpload.objects.get(name='mozball.zip')
        assert_no_validation_errors(fu)
        assert fu.validation
        validation = json.loads(fu.validation)

        eq_(validation['success'], False)
        eq_(validation['errors'], 0)

    def test_redirect(self):
        r = self.post()
        upload = FileUpload.objects.get()
        url = reverse('mkt.developers.upload_detail', args=[upload.pk, 'json'])
        self.assertRedirects(r, url)


class TestUploadDetail(BaseUploadTest):
    fixtures = ['base/apps', 'base/appversion', 'base/platforms', 'base/users']

    def setUp(self):
        super(TestUploadDetail, self).setUp()
        assert self.client.login(username='regular@mozilla.com',
                                 password='password')

    def post(self):
        # Has to be a binary, non xpi file.
        data = open(get_image_path('animated.png'), 'rb')
        return self.client.post(reverse('mkt.developers.upload'),
                                {'upload': data})

    def validation_ok(self):
        return {
            'errors': 0,
            'success': True,
            'warnings': 0,
            'notices': 0,
            'message_tree': {},
            'messages': [],
            'rejected': False,
            'metadata': {}}

    def upload_file(self, name):
        with self.file(name) as f:
            r = self.client.post(reverse('mkt.developers.upload'),
                                 {'upload': f})
        eq_(r.status_code, 302)

    def file_content(self, name):
        with self.file(name) as fp:
            return fp.read()

    @contextmanager
    def file(self, name):
        fn = os.path.join(settings.ROOT, 'mkt', 'developers', 'tests',
                          'addons', name)
        with open(fn, 'rb') as fp:
            yield fp

    @attr('validator')
    def test_detail_json(self):
        self.post()

        upload = FileUpload.objects.get()
        r = self.client.get(reverse('mkt.developers.upload_detail',
                                    args=[upload.uuid, 'json']))
        eq_(r.status_code, 200)
        data = json.loads(r.content)
        assert_no_validation_errors(data)
        eq_(data['url'],
            reverse('mkt.developers.upload_detail', args=[upload.uuid,
                                                          'json']))
        eq_(data['full_report_url'],
            reverse('mkt.developers.upload_detail', args=[upload.uuid]))
        # We must have tiers
        assert len(data['validation']['messages'])
        msg = data['validation']['messages'][0]
        eq_(msg['tier'], 1)

    @mock.patch('mkt.developers.tasks.requests.get')
    @mock.patch('mkt.developers.tasks.run_validator')
    def test_detail_for_free_extension_webapp(self, validator_mock,
                                              requests_mock):
        content = self.file_content('mozball.owa')
        response_mock = mock.Mock(status_code=200)
        response_mock.iter_content.return_value = mock.Mock(
            next=lambda: content)
        response_mock.headers = {'content-type': self.content_type}
        yield response_mock
        requests_mock.return_value = response_mock

        validator_mock.return_value = json.dumps(self.validation_ok())
        self.upload_file('mozball.owa')
        upload = FileUpload.objects.get()
        tasks.fetch_manifest('http://xx.com/manifest.owa', upload.pk)

        r = self.client.get(reverse('mkt.developers.upload_detail',
                                    args=[upload.uuid, 'json']))
        data = json.loads(r.content)
        eq_(data['validation']['messages'], [])  # no errors
        assert_no_validation_errors(data)  # no exception
        eq_(r.status_code, 200)
        eq_(data['url'],
            reverse('mkt.developers.upload_detail', args=[upload.uuid,
                                                          'json']))
        eq_(data['full_report_url'],
            reverse('mkt.developers.upload_detail', args=[upload.uuid]))

    def test_detail_view(self):
        self.post()
        upload = FileUpload.objects.get(name='animated.png')
        r = self.client.get(reverse('mkt.developers.upload_detail',
                                    args=[upload.uuid]))
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(doc('header h1').text(), 'Validation Results for animated.png')
        suite = doc('#addon-validator-suite')
        eq_(suite.attr('data-validateurl'),
            reverse('mkt.developers.standalone_upload_detail',
                    args=['hosted', upload.uuid]))
        eq_(suite('#suite-results-tier-2').length, 1)


def assert_json_error(request, field, msg):
    eq_(request.status_code, 400)
    eq_(request['Content-Type'], 'application/json')
    field = '__all__' if field is None else field
    content = json.loads(request.content)
    assert field in content, '%r not in %r' % (field, content)
    eq_(content[field], [msg])


def assert_json_field(request, field, msg):
    eq_(request.status_code, 200)
    eq_(request['Content-Type'], 'application/json')
    content = json.loads(request.content)
    assert field in content, '%r not in %r' % (field, content)
    eq_(content[field], msg)


class TestDeleteApp(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.webapp = Webapp.objects.get(id=337141)
        self.url = self.webapp.get_dev_url('delete')
        self.versions_url = self.webapp.get_dev_url('versions')
        self.dev_url = reverse('mkt.developers.apps')
        self.client.login(username='admin@mozilla.com', password='password')

    def test_delete_get(self):
        eq_(self.client.get(self.url).status_code, 405)

    def test_delete_nonincomplete(self):
        r = self.client.post(self.url)
        self.assertRedirects(r, self.dev_url)
        eq_(Addon.objects.count(), 0, 'App should have been deleted.')

    def test_delete_incomplete(self):
        self.webapp.update(status=amo.STATUS_NULL)
        r = self.client.post(self.url)
        self.assertRedirects(r, self.dev_url)
        eq_(Addon.objects.count(), 0, 'App should have been deleted.')

    def test_delete_incomplete_manually(self):
        webapp = amo.tests.addon_factory(type=amo.ADDON_WEBAPP, name='Boop',
                                         status=amo.STATUS_NULL)
        eq_(list(Webapp.objects.filter(id=webapp.id)), [webapp])
        webapp.delete('POOF!')
        eq_(list(Webapp.objects.filter(id=webapp.id)), [],
            'App should have been deleted.')

    def check_delete_redirect(self, src, dst):
        r = self.client.post(urlparams(self.url, to=src))
        self.assertRedirects(r, dst)
        eq_(Addon.objects.count(), 0, 'App should have been deleted.')

    def test_delete_redirect_to_dashboard(self):
        self.check_delete_redirect(self.dev_url, self.dev_url)

    def test_delete_redirect_to_dashboard_with_qs(self):
        url = self.dev_url + '?sort=created'
        self.check_delete_redirect(url, url)

    def test_form_action_on_status_page(self):
        # If we started on app's Manage Status page, upon deletion we should
        # be redirecte to the Dashboard.
        r = self.client.get(self.versions_url)
        eq_(pq(r.content)('.modal-delete form').attr('action'), self.url)
        self.check_delete_redirect('', self.dev_url)


class TestEnableDisable(amo.tests.TestCase):
    fixtures = fixture('webapp_337141', 'user_2519')

    def setUp(self):
        self.webapp = Webapp.objects.get(id=337141)
        self.enable_url = self.webapp.get_dev_url('enable')
        self.disable_url = self.webapp.get_dev_url('disable')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def test_get(self):
        eq_(self.client.get(self.enable_url).status_code, 405)
        eq_(self.client.get(self.disable_url).status_code, 405)

    def test_not_allowed(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.enable_url))
        self.assertLoginRequired(self.client.get(self.disable_url))

    def test_enable(self):
        self.webapp.update(disabled_by_user=True)
        self.client.post(self.enable_url)
        eq_(self.webapp.reload().disabled_by_user, False)

    def test_disable(self):
        self.client.post(self.disable_url)
        eq_(self.webapp.reload().disabled_by_user, True)


class TestRemoveLocale(amo.tests.TestCase):
    fixtures = ['base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.webapp = Addon.objects.no_cache().get(id=337141)
        self.url = self.webapp.get_dev_url('remove-locale')
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')

    def test_bad_request(self):
        r = self.client.post(self.url)
        eq_(r.status_code, 400)

    def test_success(self):
        self.webapp.name = {'en-US': 'woo', 'el': 'yeah'}
        self.webapp.save()
        self.webapp.remove_locale('el')
        r = self.client.post(self.url, {'locale': 'el'})
        eq_(r.status_code, 200)
        qs = list(Translation.objects.filter(localized_string__isnull=False)
                  .values_list('locale', flat=True)
                  .filter(id=self.webapp.name_id))
        eq_(qs, ['en-US'])

    def test_delete_default_locale(self):
        r = self.client.post(self.url, {'locale': self.webapp.default_locale})
        eq_(r.status_code, 400)


class TestTerms(amo.tests.TestCase):
    fixtures = ['base/users']

    def setUp(self):
        self.user = self.get_user()
        self.client.login(username=self.user.email, password='password')
        self.url = reverse('mkt.developers.apps.terms')

    def get_user(self):
        return UserProfile.objects.get(email='regular@mozilla.com')

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_accepted(self):
        self.user.update(read_dev_agreement=datetime.datetime.now())
        res = self.client.get(self.url)
        doc = pq(res.content)
        eq_(doc('#dev-agreement').length, 1)
        eq_(doc('#agreement-form').length, 0)

    def test_not_accepted(self):
        self.user.update(read_dev_agreement=None)
        res = self.client.get(self.url)
        doc = pq(res.content)
        eq_(doc('#dev-agreement').length, 1)
        eq_(doc('#agreement-form').length, 1)

    def test_accept(self):
        self.user.update(read_dev_agreement=None)
        res = self.client.post(self.url, {'read_dev_agreement': 'yeah'})
        eq_(res.status_code, 200)
        assert self.get_user().read_dev_agreement

    @mock.patch.object(settings, 'DEV_AGREEMENT_LAST_UPDATED',
                       amo.tests.days_ago(-5).date())
    def test_update(self):
        past = self.days_ago(10)
        self.user.update(read_dev_agreement=past)
        res = self.client.post(self.url, {'read_dev_agreement': 'yeah'})
        eq_(res.status_code, 200)
        assert self.get_user().read_dev_agreement != past

    @mock.patch.object(settings, 'DEV_AGREEMENT_LAST_UPDATED',
                       amo.tests.days_ago(-5).date())
    def test_past(self):
        past = self.days_ago(10)
        self.user.update(read_dev_agreement=past)
        res = self.client.get(self.url)
        doc = pq(res.content)
        eq_(doc('#site-notice').length, 1)
        eq_(doc('#dev-agreement').length, 1)
        eq_(doc('#agreement-form').length, 1)

    def test_not_past(self):
        res = self.client.get(self.url)
        doc = pq(res.content)
        eq_(doc('#site-notice').length, 0)
        eq_(doc('#dev-agreement').length, 1)
        eq_(doc('#agreement-form').length, 0)

    def test_l10n_good(self):
        for locale in ('en-US', 'es', 'pl'):
            res = self.client.get(self.url, {'lang': locale})
            eq_(res.status_code, 200)
            self.assertTemplateUsed(res, 'dev-agreement/%s.html' % locale)

    def test_l10n_fallback(self):
        res = self.client.get(self.url, {'lang': 'swag'})
        eq_(res.status_code, 200)
        self.assertTemplateUsed(res, 'dev-agreement/en-US.html')

    def test_redirect_to_relative(self):
        api_url = reverse('mkt.developers.apps.api')
        res = self.client.post(urlparams(self.url, to=api_url),
                               {'read_dev_agreement': 'yeah'})
        self.assert3xx(res, api_url)

    def test_redirect_to_external(self):
        res = self.client.post(urlparams(self.url, to='https://hy.fr'),
                               {'read_dev_agreement': 'yeah'})
        eq_(res.status_code, 200)


class TestTransactionList(amo.tests.TestCase):
    fixtures = fixture('user_999')

    def setUp(self):
        """Create and set up apps for some filtering fun."""
        self.create_switch(name='view-transactions')
        self.url = reverse('mkt.developers.transactions')
        self.client.login(username='regular@mozilla.com', password='password')

        self.apps = [app_factory(), app_factory()]
        self.user = UserProfile.objects.get(id=999)
        for app in self.apps:
            AddonUser.objects.create(addon=app, user=self.user)

        # Set up transactions.
        tx0 = Contribution.objects.create(addon=self.apps[0],
                                          type=amo.CONTRIB_PURCHASE,
                                          user=self.user,
                                          uuid=12345)
        tx1 = Contribution.objects.create(addon=self.apps[1],
                                          type=amo.CONTRIB_REFUND,
                                          user=self.user,
                                          uuid=67890)
        tx0.update(created=datetime.date(2011, 12, 25))
        tx1.update(created=datetime.date(2012, 1, 1))
        self.txs = [tx0, tx1]

    def test_200(self):
        r = self.client.get(self.url)
        eq_(r.status_code, 200)

    def test_own_apps(self):
        """Only user's transactions are shown."""
        app_factory()
        r = RequestFactory().get(self.url)
        r.user = self.user
        transactions = _get_transactions(r)[1]
        self.assertSetEqual([tx.addon for tx in transactions], self.apps)

    def test_filter(self):
        """For each field in the form, run it through view and check results.
        """
        tx0 = self.txs[0]
        tx1 = self.txs[1]

        self.do_filter(self.txs)
        self.do_filter(self.txs, transaction_type='None', app='oshawott')

        self.do_filter([tx0], app=tx0.addon.id)
        self.do_filter([tx1], app=tx1.addon.id)

        self.do_filter([tx0], transaction_type=tx0.type)
        self.do_filter([tx1], transaction_type=tx1.type)

        self.do_filter([tx0], transaction_id=tx0.uuid)
        self.do_filter([tx1], transaction_id=tx1.uuid)

        self.do_filter(self.txs, date_from=datetime.date(2011, 12, 1))
        self.do_filter([tx1], date_from=datetime.date(2011, 12, 30),
                       date_to=datetime.date(2012, 2, 1))

    def do_filter(self, expected_txs, **kw):
        """Checks that filter returns the expected ids

        expected_ids -- list of app ids expected in the result.
        """
        qs = _filter_transactions(Contribution.objects.all(), kw)

        self.assertSetEqual(qs.values_list('id', flat=True),
                            [tx.id for tx in expected_txs])


class TestContentRatings(amo.tests.TestCase):
    fixtures = fixture('user_admin', 'user_admin_group', 'group_admin')

    def setUp(self):
        self.create_switch('iarc')
        self.app = app_factory()
        self.app.latest_version.update(
            _developer_name='Lex Luthor <lex@kryptonite.org>')
        self.user = UserProfile.objects.get()
        self.url = reverse('mkt.developers.apps.ratings',
                           args=[self.app.app_slug])
        self.req = amo.tests.req_factory_factory(self.url, user=self.user)
        self.req.session = mock.MagicMock()

    @override_settings(IARC_SUBMISSION_ENDPOINT='https://yo.lo',
                       IARC_STOREFRONT_ID=1, IARC_PLATFORM='Firefox',
                       IARC_PASSWORD='s3kr3t')
    def test_edit(self):
        r = content_ratings_edit(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content)

        # Check the form action.
        form = doc('#ratings-edit form')[0]
        eq_(form.action, 'https://yo.lo')

        # Check the hidden form values.
        values = dict(form.form_values())
        eq_(values['storefront'], '1')
        # Note: The HTML is actually double escaped but pyquery shows it how it
        # will be send to IARC, which is singly escaped.
        eq_(values['company'], 'Lex Luthor &lt;lex@kryptonite.org&gt;')
        eq_(values['email'], self.user.email)
        eq_(values['appname'], get_iarc_app_title(self.app))
        eq_(values['platform'], 'Firefox')
        eq_(values['token'], self.app.iarc_token())
        eq_(values['pingbackurl'],
            absolutify(reverse('content-ratings-pingback',
                               args=[self.app.app_slug])))

    def test_edit_default_locale(self):
        """Ensures the form uses the app's default locale."""
        self.app.name = {'es': u'Español', 'en-US': 'English'}
        self.app.default_locale = 'es'
        self.app.save()

        r = content_ratings_edit(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content.decode('utf-8'))
        eq_(u'Español' in
            dict(doc('#ratings-edit form')[0].form_values())['appname'],
            True)

        self.app.update(default_locale='en-US')
        r = content_ratings_edit(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content.decode('utf-8'))
        eq_(u'English' in
            dict(doc('#ratings-edit form')[0].form_values())['appname'],
            True)

    def test_summary(self):
        rbs = mkt.ratingsbodies
        ratings = [rbs.CLASSIND_L, rbs.GENERIC_3, rbs.USK_18, rbs.ESRB_M,
                   rbs.PEGI_12]
        for rating in ratings:
            ContentRating.objects.create(
                addon=self.app, ratings_body=rating.ratingsbody.id,
                rating=rating.id)

        r = content_ratings(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content)

        for i, name in enumerate(doc('.name')):
            eq_(name.text, ratings[i].ratingsbody.name)

    def test_edit_iarc_app_form(self):
        r = content_ratings_edit(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content)
        assert not doc('#id_submission_id').attr('value')
        assert not doc('#id_security_code').attr('value')

        self.app.set_iarc_info(1234, 'abcd')
        r = content_ratings_edit(self.req, app_slug=self.app.app_slug)
        doc = pq(r.content)
        eq_(doc('#id_submission_id').attr('value'), '1234')
        eq_(doc('#id_security_code').attr('value'), 'abcd')


class TestContentRatingsSuccessMsg(amo.tests.TestCase):

    def setUp(self):
        self.app = app_factory(status=amo.STATUS_NULL)

    def _make_complete(self, complete_errs):
        complete_errs.return_value = {}

    def _rate_app(self):
        self.app.content_ratings.create(ratings_body=0, rating=0)

    def test_create_rating_still_incomplete(self):
        self._rate_app()
        eq_(_ratings_success_msg(self.app, amo.STATUS_NULL, None),
            _submission_msgs()['content_ratings_saved'])

    @mock.patch('mkt.webapps.models.Webapp.completion_errors')
    def test_create_rating_now_complete(self, complete_errs):
        self._rate_app()
        self.app.update(status=amo.STATUS_PENDING)
        eq_(_ratings_success_msg(self.app, amo.STATUS_NULL, None),
            _submission_msgs()['complete'])

    @mock.patch('mkt.webapps.models.Webapp.completion_errors')
    def test_create_rating_public_app(self, complete_errs):
        self._rate_app()
        self.app.update(status=amo.STATUS_PUBLIC)
        eq_(_ratings_success_msg(self.app, amo.STATUS_PUBLIC, None),
            _submission_msgs()['content_ratings_saved'])

    @mock.patch('mkt.webapps.models.Webapp.completion_errors')
    def test_update_rating_still_complete(self, complete_errs):
        self._rate_app()
        self.app.update(status=amo.STATUS_PENDING)
        eq_(_ratings_success_msg(self.app, amo.STATUS_PENDING,
                                 self.days_ago(5)),
            _submission_msgs()['content_ratings_saved'])
