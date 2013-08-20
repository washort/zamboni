from django.core.files.base import File
from django.core.files.storage import default_storage as storage
from django.db import IntegrityError
from django.http import HttpResponse
from django.utils.datastructures import MultiValueDictKeyError

from PIL import Image

from rest_framework import exceptions, generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from amo.storage_utils import copy_stored_file
from amo.utils import HttpResponseSendFile

from mkt.api.authentication import (RestOAuthAuthentication,
                                    RestAnonymousAuthentication,
                                    RestSharedSecretAuthentication)
from mkt.api.base import CORSMixin
from mkt.collections.serializers import CollectionImageSerializer
from mkt.webapps.models import Webapp

from .authorization import PublisherAuthorization
from .filters import CollectionFilterSetWithFallback
from .models import Collection
from .serializers import CollectionMembershipField, CollectionSerializer


class CollectionViewSet(CORSMixin, viewsets.ModelViewSet):
    serializer_class = CollectionSerializer
    queryset = Collection.objects.all()
    cors_allowed_methods = ('get', 'post', 'delete')
    permission_classes = [PublisherAuthorization]
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication,
                              RestAnonymousAuthentication]
    filter_class = CollectionFilterSetWithFallback

    exceptions = {
        'not_provided': '`app` was not provided.',
        'doesnt_exist': '`app` does not exist.',
        'not_in': '`app` not in collection.',
        'already_in': '`app` already exists in collection.',
        'app_mismatch': 'All apps in this collection must be included.'
    }

    def return_updated(self, status):
        """
        Passed an HTTP status from rest_framework.status, returns a response
        of that status with the body containing the updated values of
        self.object.
        """
        collection = self.get_object()
        serializer = self.get_serializer(instance=collection)
        return Response(serializer.data, status=status)

    @action()
    def add_app(self, request, pk=None):
        """
        Add an app to the specified collection.
        """
        collection = self.get_object()
        try:
            new_app = Webapp.objects.get(pk=request.DATA['app'])
        except (KeyError, MultiValueDictKeyError):
            raise exceptions.ParseError(detail=self.exceptions['not_provided'])
        except Webapp.DoesNotExist:
            raise exceptions.ParseError(detail=self.exceptions['doesnt_exist'])
        try:
            collection.add_app(new_app)
        except IntegrityError:
            raise exceptions.ParseError(detail=self.exceptions['already_in'])
        return self.return_updated(status.HTTP_200_OK)

    @action()
    def remove_app(self, request, pk=None):
        """
        Remove an app from the specified collection.
        """
        collection = self.get_object()
        try:
            to_remove = Webapp.objects.get(pk=request.DATA['app'])
        except (KeyError, MultiValueDictKeyError):
            raise exceptions.ParseError(detail=self.exceptions['not_provided'])
        except Webapp.DoesNotExist:
            raise exceptions.ParseError(detail=self.exceptions['doesnt_exist'])
        removed = collection.remove_app(to_remove)
        if not removed:
            raise exceptions.ParseError(detail=self.exceptions['not_in'])
        return self.return_updated(status.HTTP_200_OK)

    @action()
    def reorder(self, request, pk=None):
        """
        Reorder the specified collection.
        """
        collection = self.get_object()
        try:
            collection.reorder(request.DATA)
        except ValueError:
            return Response({
                'detail': self.exceptions['app_mismatch'],
                'apps': [CollectionMembershipField().to_native(a) for a in
                         collection.collectionmembership_set.all()]
            }, status=status.HTTP_400_BAD_REQUEST, exception=True)
        return self.return_updated(status.HTTP_200_OK)


class CollectionImageViewSet(CORSMixin, viewsets.ViewSet, generics.RetrieveUpdateAPIView):
    serializer_class = CollectionImageSerializer
    queryset = Collection.objects.all()
    permission_classes = [PublisherAuthorization]
    authentication_classes = [RestOAuthAuthentication,
                              RestAnonymousAuthentication]
    cors_allowed_methods = ('get', 'put')

    def retrieve(self, request, pk=None):
        obj = self.get_object()
        return HttpResponseSendFile(request, obj.image_path(),
                                    content_type='image/png')

    def update(self, request, *a, **kw):
        obj = self.get_object()
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)
        if serializer.is_valid():
            i = Image.open(serializer.data['image'])
            with storage.open(obj.image_path(), 'wb') as f:
                i.save(f, 'png')
            return Response(status=204)
        else:
            return Response(status=400)

