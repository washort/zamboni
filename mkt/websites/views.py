
from django.db.transaction import non_atomic_requests

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.viewsets import ViewSet


from mkt.api.authentication import (RestOAuthAuthentication,
                                    RestSharedSecretAuthentication)
from mkt.api.base import CORSMixin, MarketplaceView
from mkt.api.paginator import ESPaginator
from mkt.regions import get_region
from mkt.websites.indexers import WebsiteIndexer
from mkt.websites.models import store
from mkt.websites.serializers import WebsiteSerializer


class WebsiteSearchView(CORSMixin, MarketplaceView, ListAPIView):
    """
    Base website search view based on a single-string query.
    """
    cors_allowed_methods = ['get']
    authentication_classes = [RestSharedSecretAuthentication,
                              RestOAuthAuthentication]
    permission_classes = [AllowAny]
    filter_backends = []  # FIXME: SearchQueryFilter and friends.
    serializer_class = Serializer  # FIXME use a real serializer.
    paginator_class = ESPaginator

    def get_queryset(self):
        return WebsiteIndexer.search()

    @classmethod
    def as_view(cls, **kwargs):
        # Make all search views non_atomic: they should not need the db, or
        # at least they should not need to make db writes, so they don't need
        # to be wrapped in transactions.
        view = super(WebsiteSearchView, cls).as_view(**kwargs)
        return non_atomic_requests(view)


class WebsiteViewSet(ViewSet):
    cors_allowed_methods = ('get', 'put', 'post', 'delete')
    authentication_classes = [RestSharedSecretAuthentication,
                              RestOAuthAuthentication]
    permission_classes = [AllowAny]

    def retrieve(self, request, *args, **kwargs):
        region = get_region()
        ws = store.fetch_visible_website(
            region=region.id,
            user=self.request.user,
            pk=int(self.kwargs['pk']))
        if ws is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(WebsiteSerializer(ws).data)

    def create(self, request, *args, **kwargs):
        pass

    def update(self, request, *args, **kwargs):
        pass

    def destroy(self, request, *args, **kwargs):
        pass
