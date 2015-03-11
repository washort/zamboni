from django.conf import settings
from django_statsd.clients import statsd

from elasticsearch_dsl import filter as es_filter
from elasticsearch_dsl import function as es_function
from elasticsearch_dsl import query, Search

import mkt
import mkt.feed.constants as feed
from mkt.feed.indexers import FeedItemIndexer
from mkt.webapps.indexers import WebappIndexer


def get_es_feed_query(sq, region=mkt.regions.RESTOFWORLD.id,
                      carrier=None, original_region=None):
    """
    Build ES query for feed.
    Must match region.
    Orders by FeedItem.order.
    Boosted operator shelf matching region + carrier.
    Boosted operator shelf matching original_region + carrier.

    region -- region ID (integer)
    carrier -- carrier ID (integer)
    original_region -- region from before we were falling back,
        to keep the original shelf atop the RoW feed.
    """
    region_filter = es_filter.Term(region=region)
    shelf_filter = es_filter.Term(item_type=feed.FEED_TYPE_SHELF)

    ordering_fn = es_function.FieldValueFactor(
        field='order', modifier='reciprocal',
        filter=es_filter.Bool(must=[region_filter],
                              must_not=[shelf_filter]))
    boost_fn = es_function.BoostFactor(value=10000.0,
                                       filter=shelf_filter)

    if carrier is None:
        # If no carrier, just match the region and exclude shelves.
        return sq.query('function_score',
                        functions=[ordering_fn],
                        filter=es_filter.Bool(
                            must=[region_filter],
                            must_not=[shelf_filter]
                        ))

    # Must match region.
    # But also include the original region if we falling back to RoW.
    # The only original region feed item that will be included is a shelf
    # else we wouldn't be falling back in the first place.
    region_filters = [region_filter]
    if original_region:
        region_filters.append(es_filter.Term(region=original_region))

    return sq.query(
        'function_score',
        functions=[boost_fn, ordering_fn],
        filter=es_filter.Bool(
            should=region_filters,
            # Filter out shelves that don't match the carrier.
            must_not=[es_filter.Bool(
                must=[shelf_filter],
                must_not=[es_filter.Term(carrier=carrier)])])
    )


def get_feed_element_index():
    """Return a list of index to query all at once."""
    return [
        settings.ES_INDEXES['mkt_feed_app'],
        settings.ES_INDEXES['mkt_feed_brand'],
        settings.ES_INDEXES['mkt_feed_collection'],
        settings.ES_INDEXES['mkt_feed_shelf']
    ]


class ObjectNotFound(Exception):
    pass


class ESStore(object):
    ObjectNotFound = ObjectNotFound

    def feed_get(self, region, carrier, original_region):
        es = FeedItemIndexer.get_es()
        return get_es_feed_query(FeedItemIndexer.search(using=es),
                                 region=region, carrier=carrier,
                                 original_region=original_region)

    def fetch_feed_elements(self, feed_items):
        """
        From a list of FeedItems with normalized feed element IDs,
        return an ES query that fetches the feed elements for each feed item.
        """
        es = FeedItemIndexer.get_es()
        sq = Search(using=es, index=get_feed_element_index())
        filters = []
        for feed_item in feed_items:
            item_type = feed_item['item_type']
            filters.append(es_filter.Bool(
                must=[es_filter.Term(id=feed_item[item_type]),
                      es_filter.Term(item_type=item_type)]))

        qq = sq.filter(es_filter.Bool(should=filters))[:len(feed_items)]
        with statsd.timer('mkt.feed.view.feed_element_query'):
            return qq.execute().hits

    def fetch_app_map(self, request, app_ids, filter_backends):
        """
        Takes a list of app_ids. Gets the apps, including filters.
        Returns an app_map for serializer context.
        """
        sq = WebappIndexer.search()
        if request.QUERY_PARAMS.get('filtering', '1') == '1':
            # With filtering (default).
            for backend in filter_backends:
                sq = backend().filter_queryset(request, sq, self)
        sq = WebappIndexer.filter_by_apps(app_ids, sq)

        # Store the apps to attach to feed elements later.
        with statsd.timer('mkt.feed.views.apps_query'):
            apps = sq.execute().hits
        return dict((app.id, app) for app in apps)

    def search_feed(self, q):
                # Make search.
        queries = [
            query.Q('match', slug=self._phrase(q)),  # Slug.
            query.Q('match', type=self._phrase(q)),  # Type.
            query.Q('match', search_names=self._phrase(q)),  # Name.
            query.Q('prefix', carrier=q),  # Shelf carrier.
            query.Q('term', region=q)  # Shelf region.
        ]
        sq = query.Bool(should=queries)

        # Search.
        es = Search(using=FeedItemIndexer.get_es(),
                    index=self.get_feed_element_index())
        return es.query(sq).execute().hits

    def fetch_single_feed_element(self, item_type, slug):
                # Hit ES.
        sq = self.get_feed_element_filter(
            Search(using=FeedItemIndexer.get_es(),
                   index=self.INDICES[item_type]),
            item_type, slug)
        try:
            return sq.execute().hits[0]
        except IndexError:
            raise ObjectNotFound()

    def fetch_recent_feed_elements(self, index):
        sq = Search(using=FeedItemIndexer.get_es(), index=index)
        return sq.sort('-created').query(query.MatchAll())


store = ESStore()
