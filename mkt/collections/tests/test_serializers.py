# -*- coding: utf-8 -*-
from nose.tools import eq_, ok_

import amo.tests
from mkt.collections.models import Collection
from mkt.collections.serializers import (CollectionImageSerializer,
                                         CollectionMembershipField,
                                         CollectionSerializer,)
from mkt.webapps.utils import app_to_dict


class CollectionDataMixin(object):
    collection_data = {
        'collection_type': 0,
        'name': 'My Favorite Games',
        'description': 'A collection of my favorite games',
    }


class TestCollectionMembershipField(CollectionDataMixin, amo.tests.TestCase):

    def setUp(self):
        self.collection = Collection.objects.create(**self.collection_data)
        self.app = amo.tests.app_factory()
        self.collection.add_app(self.app)
        self.field = CollectionMembershipField()

    def test_to_native(self):
        membership = self.collection.collectionmembership_set.all()[0]
        native = self.field.to_native(membership)
        eq_(native, app_to_dict(self.app))


class TestCollectionSerializer(CollectionDataMixin, amo.tests.TestCase):

    def setUp(self):
        self.collection = Collection.objects.create(**self.collection_data)
        self.serializer = CollectionSerializer()

    def test_to_native(self, apps=None):
        if apps:
            for app in apps:
                self.collection.add_app(app)
        else:
            apps = []

        data = self.serializer.to_native(self.collection)
        for name, value in self.collection_data.iteritems():
            eq_(self.collection_data[name], data[name])
        self.assertSetEqual(data.keys(), ['id', 'name', 'description', 'apps',
                                          'collection_type', 'category',
                                          'region', 'carrier', 'author',
                                          'is_public'])
        for order, app in enumerate(apps):
            eq_(data['apps'][order]['slug'], app.app_slug)

    def test_translation_deserialization(self):
        data = {
            'name': u'¿Dónde está la biblioteca?'
        }
        serializer = CollectionSerializer(instance=self.collection, data=data,
                                          partial=True)
        eq_(serializer.errors, {})
        ok_(serializer.is_valid())

    def test_translation_deserialization_multiples_locales(self):
        data = {
            'name': {
                'fr': u'Chat grincheux…',
                'en-US': u'Grumpy Cat...'
            }
        }
        serializer = CollectionSerializer(instance=self.collection, data=data,
                                          partial=True)
        eq_(serializer.errors, {})
        ok_(serializer.is_valid())

    def test_to_native_with_apps(self):
        apps = [amo.tests.app_factory() for n in xrange(1, 5)]
        self.test_to_native(apps=apps)


IMAGE_DATA = """
R0lGODlhKAAoAPMAAP////vzBf9kA90JB/IIhEcApQAA0wKr6h+3FABkElYsBZBxOr+/v4CAgEBA
QAAAACH/C05FVFNDQVBFMi4wAwEAAAAh/h1HaWZCdWlsZGVyIDAuMiBieSBZdmVzIFBpZ3VldAAh
+QQECgD/ACwAAAAAKAAoAEMEx5DJSSt9z+rNcfgf5oEBxlVjWIreQ77wqqWrW8e4fKJ2ru9ACS2U
CW6GIBaSOOu9lMknK2dqrog2pYhp7Dir3fAIHN4tk8XyBKmFkU9j0tQnT6+d2K2qrnen2W10MW93
WIZogGJ4dIRqZ41qTZCRXpOUPHWXXjiWioKdZniBaI6LNX2ZQS1aLnOcdhYpPaOfsAxDrXOiqKlL
rL+0mb5Qg7ypQru5Z1S2yIiHaK9Aq1lfxFxGLYe/P2XLUprOzOGY4ORW3edNkREAIfkEBAoA/wAs
AAAAACgAKABDBMqQyUkrfc/qzXH4YBhiXOWNAaZ6q+iS1vmps1y3Y1aaj/vqu6DEVhN2einfipgC
XpA/HNRHbW5YSFpzmXUaY1PYd3wSj3fM3JlXrZpLsrIc9wNHW71pGyRmcpM0dHUaczc5WnxeaHp7
b2sMaVaPQSuTZCqWQjaOmUOMRZ2ee5KTkVSci22CoJRQiDeviXBhh1yfrBNEWH+jspC3S3y9dWnB
sb1muru1x6RshlvMeqhP0U3Sal8s0LZ5ikamItTat7ihft+hv+bqYI8RADs=
"""


class TestCollectionImageSerializer(amo.tests.TestCase):

    def setUp(self):
        self.collection_data = {
            'name': 'My Favorite Games',
            'description': 'A collection of my favorite games',
        }
        self.collection = Collection.objects.create(**self.collection_data)
        self.serializer = CollectionImageSerializer()

    def test_to_native(self):
        d = self.serializer.from_native({'image': 'data:image/gif;base64,' +
                                         IMAGE_DATA}, None)
        eq_(d['image'].read(), IMAGE_DATA.decode('base64'))
