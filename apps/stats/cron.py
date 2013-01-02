import datetime

from django.core.management import call_command
from django.db.models import Sum, Max

import commonware.log
from celery.task.sets import TaskSet
import cronjobs

from amo.utils import chunked
from addons.models import Addon
from .models import (AddonCollectionCount, CollectionCount,
                     UpdateCount)
from . import tasks
from lib.es.utils import raise_if_reindex_in_progress

task_log = commonware.log.getLogger('z.task')
cron_log = commonware.log.getLogger('z.cron')


@cronjobs.register
def update_addons_collections_downloads():
    """Update addons+collections download totals."""
    raise_if_reindex_in_progress()

    d = (AddonCollectionCount.objects.values('addon', 'collection')
         .annotate(sum=Sum('count')))

    ts = [tasks.update_addons_collections_downloads.subtask(args=[chunk])
          for chunk in chunked(d, 100)]
    TaskSet(ts).apply_async()


@cronjobs.register
def update_collections_total():
    """Update collections downloads totals."""

    d = (CollectionCount.objects.values('collection_id')
                                .annotate(sum=Sum('count')))

    ts = [tasks.update_collections_total.subtask(args=[chunk])
          for chunk in chunked(d, 50)]
    TaskSet(ts).apply_async()


@cronjobs.register
def update_global_totals(date=None):
    """Update global statistics totals."""
    raise_if_reindex_in_progress()

    if date:
        date = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    today = date or datetime.date.today()
    today_jobs = [dict(job=job, date=today) for job in
                  tasks._get_daily_jobs(date)]

    max_update = date or UpdateCount.objects.aggregate(max=Max('date'))['max']
    metrics_jobs = [dict(job=job, date=max_update) for job in
                    tasks._get_metrics_jobs(date)]

    ts = [tasks.update_global_totals.subtask(kwargs=kw)
          for kw in today_jobs + metrics_jobs]
    TaskSet(ts).apply_async()


WEBTRENDS_URLS = [
    'https://ws.webtrends.com/v3/Reporting/profiles/46543/'
    'reports/VCjJhvO2mL5/?totals=all&period_type=agg&measures=7']

@cronjobs.register
def update_webtrends(date=None, urlbases=WEBTRENDS_URLS):
    """
    Update stats from Webtrends.
    """
    if date:
        date = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    else:
        # Assume that we want to populate yesterdays stats by default.
        date = datetime.date.today() - datetime.timedelta(days=1)
    datestr = date.strftime('%Ym%md%d')
    urls = ['%s&start_period=%s&end_period=%s&format=json'
            % (u, datestr, datestr) for u in urlbases]
    TaskSet([tasks.update_webtrend.subtask(kwargs={'date': date, 'url': u})
             for u in urls]).apply_async()

GOOGLE_ANALYTICS_METRICS = ('ga:visits',)

@cronjobs.register
def update_google_analytics(date=None, metrics=GOOGLE_ANALYTICS_METRICS):
    """
    Update stats from Webtrends.
    """
    if date:
        date = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    else:
        # Assume that we want to populate yesterday's stats by default.
        date = datetime.date.today() - datetime.timedelta(days=1)
    TaskSet([tasks.update_google_analytics.subtask(kwargs={'date': date,
                                                           'metric': u})
             for u in metrics]).apply_async()


@cronjobs.register
def addon_total_contributions():
    addons = Addon.objects.values_list('id', flat=True)
    ts = [tasks.addon_total_contributions.subtask(args=chunk)
          for chunk in chunked(addons, 100)]
    TaskSet(ts).apply_async()


@cronjobs.register
def index_latest_stats(index=None, aliased=True):
    raise_if_reindex_in_progress()
    latest = UpdateCount.search(index).order_by('-date').values_dict()
    if latest:
        latest = latest[0]['date']
    else:
        latest = datetime.date.today() - datetime.timedelta(days=1)
    fmt = lambda d: d.strftime('%Y-%m-%d')
    date_range = '%s:%s' % (fmt(latest), fmt(datetime.date.today()))
    cron_log.info('index_stats --date=%s' % date_range)
    call_command('index_stats', addons=None, date=date_range)
