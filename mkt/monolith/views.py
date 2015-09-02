import datetime
import logging

from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from mkt.abuse.models import AbuseReport
from mkt.api.authentication import RestOAuthAuthentication
from mkt.api.base import CORSMixin, MarketplaceView
from mkt.api.permissions import GroupPermission
from mkt.ratings.models import Review

from .forms import MonolithForm
from .models import MonolithRecord
from .serializers import MonolithSerializer

log = logging.getLogger('z.monolith')


# TODO: Move the stats that can be calculated on the fly from
# apps/stats/tasks.py here.
STATS = {
    'apps_ratings': {
        'qs': Review.objects
        .filter(editorreview=0)
        .values('webapp')
        .annotate(count=Count('webapp')),
        'type': 'slice',
        'field_map': {
            'count': 'count',
            'app-id': 'webapp'},
    },
    'apps_average_rating': {
        'qs': Review.objects
        .filter(editorreview=0)
        .values('webapp')
        .annotate(avg=Avg('rating')),
        'type': 'total',
        'field_map': {
            'count': 'avg',
            'app-id': 'webapp'},
    },
    'apps_abuse_reports': {
        'qs': AbuseReport.objects
        .values('webapp')
        .annotate(count=Count('webapp')),
        'type': 'slice',
        'field_map': {
            'count': 'count',
            'app-id': 'webapp'},
    }
}


def daterange(start, end):
    for n in range((end - start).days):
        yield start + datetime.timedelta(n)


def _get_query_result(key, start, end):
    # To do on-the-fly queries we have to produce results as if they
    # were calculated daily, which means we need to iterate over each
    # day in the range and perform an aggregation on this date.

    data = []
    today = datetime.date.today()
    stat = STATS[key]

    # Choose start and end dates that make sense if none provided.
    if not start:
        raise ParseError('`start` was not provided')
    if not end:
        end = today

    for day in daterange(start, end):
        if stat['type'] == 'total':
            # If it's a totalling queryset, we want to filter by the
            # end date until the beginning of time to get the total
            # objects up until this point in time.
            date_filtered = stat['qs'].filter(
                created__lt=(day + datetime.timedelta(days=1)))
        else:
            # Otherwise, we want to filter by both start/end to get
            # counts on a specific day.
            date_filtered = stat['qs'].filter(
                created__gte=day,
                created__lt=(day + datetime.timedelta(days=1)))

        data.extend([{
            'key': key,
            'recorded': day,
            'user_hash': None,
            'value': {'count': d.get(stat['field_map']['count']),
                      'app-id': d.get(stat['field_map']['app-id'])}}
                     for d in date_filtered])

    return data


class MonolithView(CORSMixin, MarketplaceView, ListAPIView):
    cors_allowed_methods = ['get']
    permission_classes = [GroupPermission('Monolith', 'API')]
    authentication_classes = [RestOAuthAuthentication]
    serializer_class = MonolithSerializer

    def get_queryset(self):
        form = MonolithForm(self.request.QUERY_PARAMS)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        key = form.cleaned_data['key']
        start = form.cleaned_data['start']
        end = form.cleaned_data['end']

        log.info('[Monolith] Querying key:%s [%s:%s]' % (key, start, end))

        if key in STATS:
            return _get_query_result(key, start, end)

        else:
            qs = MonolithRecord.objects.all()
            if key:
                qs = qs.filter(key=key)
            if start is not None:
                qs = qs.filter(recorded__gte=start)
            if end is not None:
                qs = qs.filter(recorded__lt=end)

            return qs
