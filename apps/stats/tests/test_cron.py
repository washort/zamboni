import datetime

from django.conf import settings
from django.core.management import call_command

import httplib2
import mock
from nose.tools import eq_

import amo.tests
from addons.models import Addon
from mkt.webapps.models import Installed
from reviews.models import Review
from stats.models import (Contribution, DownloadCount, GlobalStat,
                          UpdateCount)
from stats import cron, tasks
from users.models import UserProfile


class TestGlobalStats(amo.tests.TestCase):
    fixtures = ['stats/test_models']

    def test_stats_for_date(self):

        date = datetime.date(2009, 6, 1)
        job = 'addon_total_downloads'

        eq_(GlobalStat.objects.no_cache().filter(date=date,
                                                 name=job).count(), 0)
        tasks.update_global_totals(job, date)
        eq_(len(GlobalStat.objects.no_cache().filter(date=date,
                                                 name=job)), 1)

    def test_marketplace_stats(self):
        res = tasks._get_daily_jobs()
        for k in ['apps_count_new', 'apps_count_installed',
                  'apps_review_count_new']:
            assert k in res, 'Job %s missing from _get_daily_jobs' % k

    def test_app_new(self):
        Addon.objects.create(type=amo.ADDON_WEBAPP)
        eq_(tasks._get_daily_jobs()['apps_count_new'](), 1)

    def test_apps_installed(self):
        addon = Addon.objects.create(type=amo.ADDON_WEBAPP)
        user = UserProfile.objects.create(username='foo')
        Installed.objects.create(addon=addon, user=user)
        eq_(tasks._get_daily_jobs()['apps_count_installed'](), 1)

    def test_app_reviews(self):
        addon = Addon.objects.create(type=amo.ADDON_WEBAPP)
        user = UserProfile.objects.create(username='foo')
        Review.objects.create(addon=addon, user=user)
        eq_(tasks._get_daily_jobs()['apps_review_count_new'](), 1)


    def test_input(self):
        for x in ['2009-1-1',
                  datetime.datetime(2009, 1, 1),
                  datetime.datetime(2009, 1, 1, 11, 0)]:
            with self.assertRaises((TypeError, ValueError)):
                tasks._get_daily_jobs(x)

    def test_user_total(self):
        day = datetime.date(2009, 1, 1)
        p = UserProfile.objects.create(username='foo',
                                       source=amo.LOGIN_SOURCE_MMO_BROWSERID)
        p.update(created=day)
        eq_(tasks._get_daily_jobs(day)['mmo_user_count_total'](), 1)
        eq_(tasks._get_daily_jobs()['mmo_user_count_total'](), 1)
        eq_(tasks._get_daily_jobs()['mmo_user_count_new'](), 0)

    def test_user_new(self):
        UserProfile.objects.create(username='foo',
                                   source=amo.LOGIN_SOURCE_MMO_BROWSERID)
        eq_(tasks._get_daily_jobs()['mmo_user_count_new'](), 1)


class TestWebtrends(amo.tests.TestCase):

    @mock.patch.object(settings, 'WEBTRENDS_USERNAME', 'username')
    @mock.patch.object(settings, 'WEBTRENDS_PASSWORD', 'passwd')
    @mock.patch('requests.get')
    def test_url(self, get):
        class FakeResponse:
            status_code = 200
            json = {u'data': [{u'measures': {u'DailyVisitors': 1581}}]}
        get.return_value = FakeResponse()
        cron.update_webtrends('2012-1-1', ['http://example.com/webtrends'])
        get.assert_called_with(
            'http://example.com/webtrends&start_period=2012m01d01'
            '&end_period=2012m01d01&format=json',
            auth=('username', 'passwd'))

        eq_(GlobalStat.objects.get(name='webtrends_DailyVisitors',
                                   date=datetime.date(2012, 1, 1)).count,
            1581)


class TestGoogleAnalytics(amo.tests.TestCase):

    def setUp(self):
        self.http = httplib2.Http()

    @mock.patch('httplib2.Http')
    @mock.patch('stats.tasks.get_first_profile_id')
    @mock.patch('stats.tasks.build')
    def test_ask_google(self, build, gfpi, http):
        http.return_value = self.http
        gfpi.return_value = '1'
        d = '2012-01-01'
        get = build('analytics', 'v3', http=http).data().ga().get(
            metrics='ga:visits', ids='ga:1',
            start_date=d, end_date=d)
        get.execute.return_value = {'rows': [[49]]}
        cron.update_google_analytics(d)
        eq_(GlobalStat.objects.get(name='google_analytics_ga:visits',
                                   date=d).count, 49)

class TestTotalContributions(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/appversion', 'base/users',
                'base/addon_3615']

    def test_total_contributions(self):

        c = Contribution()
        c.addon_id = 3615
        c.amount = '9.99'
        c.save()

        tasks.addon_total_contributions(3615)
        a = Addon.objects.no_cache().get(pk=3615)
        eq_(float(a.total_contributions), 9.99)

        c = Contribution()
        c.addon_id = 3615
        c.amount = '10.00'
        c.save()

        tasks.addon_total_contributions(3615)
        a = Addon.objects.no_cache().get(pk=3615)
        eq_(float(a.total_contributions), 19.99)


@mock.patch('stats.management.commands.index_stats.create_tasks')
class TestIndexStats(amo.tests.TestCase):
    fixtures = ['stats/test_models']

    def setUp(self):
        self.downloads = (DownloadCount.objects.order_by('-date')
                          .values_list('id', flat=True))
        self.updates = (UpdateCount.objects.order_by('-date')
                        .values_list('id', flat=True))

    def test_by_date(self, tasks_mock):
        call_command('index_stats', addons=None, date='2009-06-01')
        qs = self.downloads.filter(date='2009-06-01')
        download = tasks_mock.call_args_list[1][0]
        eq_(download[0], tasks.index_download_counts)
        eq_(download[1], list(qs))

    def test_called_three(self, tasks_mock):
        call_command('index_stats', addons=None, date='2009-06-01')
        eq_(tasks_mock.call_count, 3)

    def test_called_two(self, tasks_mock):
        call_command('index_stats', addons='5', date='2009-06-01')
        eq_(tasks_mock.call_count, 2)

    def test_by_date_range(self, tasks_mock):
        call_command('index_stats', addons=None,
                     date='2009-06-01:2009-06-07')
        qs = self.downloads.filter(date__range=('2009-06-01', '2009-06-07'))
        download = tasks_mock.call_args_list[1][0]
        eq_(download[0], tasks.index_download_counts)
        eq_(download[1], list(qs))

    def test_by_addon(self, tasks_mock):
        call_command('index_stats', addons='5', date=None)
        qs = self.downloads.filter(addon=5)
        download = tasks_mock.call_args_list[1][0]
        eq_(download[0], tasks.index_download_counts)
        eq_(download[1], list(qs))

    def test_by_addon_and_date(self, tasks_mock):
        call_command('index_stats', addons='4', date='2009-06-01')
        qs = self.downloads.filter(addon=4, date='2009-06-01')
        download = tasks_mock.call_args_list[1][0]
        eq_(download[0], tasks.index_download_counts)
        eq_(download[1], list(qs))

    def test_multiple_addons_and_date(self, tasks_mock):
        call_command('index_stats', addons='4, 5', date='2009-10-03')
        qs = self.downloads.filter(addon__in=[4, 5], date='2009-10-03')
        download = tasks_mock.call_args_list[1][0]
        eq_(download[0], tasks.index_download_counts)
        eq_(download[1], list(qs))

    def test_no_addon_or_date(self, tasks_mock):
        call_command('index_stats', addons=None, date=None)
        calls = tasks_mock.call_args_list
        updates = list(self.updates.values_list('date', flat=True))
        downloads = list(self.downloads.values_list('date', flat=True))

        # Check that we're calling the task in increments of 5 days.
        # We add 1 because picking up 11 days means we have start/stop pairs at
        # [0, 5], [5, 10], [10, 15]
        eq_(len([c for c in calls if c[0][0] == tasks.index_update_counts]),
            1 + (updates[0] - updates[-1]).days / 5)
        eq_(len([c for c in calls if c[0][0] == tasks.index_download_counts]),
            1 + (downloads[0] - downloads[-1]).days / 5)


class TestIndexLatest(amo.tests.ESTestCase):
    es = True

    def test_index_latest(self):
        latest = datetime.date.today() - datetime.timedelta(days=5)
        UpdateCount.index({'date': latest})
        self.refresh('update_counts')

        start = latest.strftime('%Y-%m-%d')
        finish = datetime.date.today().strftime('%Y-%m-%d')
        with mock.patch('stats.cron.call_command') as call:
            cron.index_latest_stats()
            call.assert_called_with('index_stats', addons=None,
                                    date='%s:%s' % (start, finish))
