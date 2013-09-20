import os

from django.conf import settings
from django.db import models

import amo.models
import mkt.regions
from addons.models import Category, clean_slug
from amo.decorators import use_master
from amo.utils import to_language
from mkt.webapps.models import Webapp
from translations.fields import PurifiedField, save_signal

from .constants import COLLECTION_TYPES
from .fields import ColorField
from .managers import PublicCollectionsManager


class Collection(amo.models.ModelBase):
    collection_type = models.IntegerField(choices=COLLECTION_TYPES)
    description = PurifiedField()
    name = PurifiedField()
    is_public = models.BooleanField(default=False)
    # FIXME: add better / composite indexes that matches the query we are
    # going to make.
    category = models.ForeignKey(Category, null=True, blank=True)
    region = models.PositiveIntegerField(default=None, null=True, blank=True,
        choices=mkt.regions.REGIONS_CHOICES_ID, db_index=True)
    carrier = models.IntegerField(default=None, null=True, blank=True,
        choices=mkt.carriers.CARRIER_CHOICES, db_index=True)
    author = models.CharField(max_length=255, default='', blank=True)
    slug = models.SlugField(blank=True, max_length=30,
                            help_text='Used in collection URLs.')
    default_language = models.CharField(max_length=10,
        choices=((to_language(lang), desc)
                 for lang, desc in settings.LANGUAGES.items()),
        default=to_language(settings.LANGUAGE_CODE))
    curators = models.ManyToManyField('users.UserProfile')
    background_color = ColorField(null=True)
    text_color = ColorField(null=True)
    has_image = models.BooleanField(default=False)

    objects = amo.models.ManagerBase()
    public = PublicCollectionsManager()

    class Meta:
        db_table = 'app_collections'
        ordering = ('-id',)  # This will change soon since we'll need to be
                             # able to order collections themselves, but this
                             # helps tests for now.

    def __unicode__(self):
        return self.name.localized_string_clean

    def save(self, **kw):
        self.clean_slug()
        return super(Collection, self).save(**kw)

    @use_master
    def clean_slug(self):
        clean_slug(self, 'slug')

    @classmethod
    def get_fallback(cls):
        return cls._meta.get_field('default_language')

    def image_path(self):
        return os.path.join(settings.COLLECTIONS_ICON_PATH,
                            str(self.pk / 1000),
                            'app_collection_%s.png' % (self.pk,))

    def apps(self):
        """
        Return a list containing all apps in this collection.
        """
        return [a.app for a in self.collectionmembership_set.all()]

    def add_app(self, app, order=None):
        """
        Add an app to this collection. If specified, the app will be created
        with the specified `order`. If not, it will be added to the end of the
        collection.
        """
        if not order:
            qs = CollectionMembership.objects.filter(collection=self)
            aggregate = qs.aggregate(models.Max('order'))['order__max']
            order = aggregate + 1 if aggregate is not None else 0
        return CollectionMembership.objects.create(collection=self, app=app,
                                                   order=order)

    def remove_app(self, app):
        """
        Remove the passed app from this collection, returning a boolean
        indicating whether a successful deletion took place.
        """
        try:
            membership = self.collectionmembership_set.get(app=app)
        except CollectionMembership.DoesNotExist:
            return False
        else:
            membership.delete()
            return True

    def reorder(self, new_order):
        """
        Passed a list of app IDs, e.g.

        [18, 24, 9]

        will change the order of each item in the collection to match the
        passed order. A ValueError will be raised if each app in the
        collection is not included in the ditionary.
        """
        if set(a.pk for a in self.apps()) != set(new_order):
            raise ValueError('Not all apps included')
        for order, pk in enumerate(new_order):
            CollectionMembership.objects.get(collection=self,
                                             app_id=pk).update(order=order)

    def has_curator(self, userprofile):
        """
        Returns boolean indicating whether the passed user profile is a curator
        on this collection.

        ID comparison used instead of directly checking objects to ensure that
        RequestUser or UserProfile objects could be passed.
        """
        return userprofile.id in self.curators.values_list('id', flat=True)

    def add_curator(self, userprofile):
        return self.curators.add(userprofile)

    def remove_curator(self, userprofile):
        return self.curators.remove(userprofile)


class CollectionMembership(amo.models.ModelBase):
    collection = models.ForeignKey(Collection)
    app = models.ForeignKey(Webapp)
    order = models.SmallIntegerField(null=True)

    def __unicode__(self):
        return u'"%s" in "%s"' % (self.app.name, self.collection.name)

    class Meta:
        db_table = 'app_collection_membership'
        unique_together = ('collection', 'app',)
        ordering = ('order',)


models.signals.pre_save.connect(save_signal, sender=Collection,
                                dispatch_uid='collection_translations')
