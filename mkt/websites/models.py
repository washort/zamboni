# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import models
from django.dispatch import receiver

import json_field

from lib.utils import static_url

from mkt.site.models import ModelBase
from mkt.tags.models import Tag
from mkt.translations.fields import save_signal, TranslatedField
from mkt.websites.indexers import WebsiteIndexer


class Website(ModelBase):
    default_locale = models.CharField(max_length=10,
                                      default=settings.LANGUAGE_CODE)
    url = TranslatedField()
    title = TranslatedField()
    short_title = TranslatedField()
    description = TranslatedField()
    _keywords = models.ManyToManyField(Tag, db_column='keywords')
    region_exclusions = json_field.JSONField(default=None)
    devices = json_field.JSONField(default=None)
    categories = json_field.JSONField(default=None)
    icon_type = models.CharField(max_length=25, blank=True)
    icon_hash = models.CharField(max_length=8, blank=True)
    last_updated = models.DateTimeField(db_index=True, auto_now_add=True)
    # FIXME status

    @classmethod
    def get_fallback(cls):
        return cls._meta.get_field('default_locale')

    @classmethod
    def get_indexer(self):
        return WebsiteIndexer

    @property
    def keywords(self):
        return [t.tag_text for t in self._keywords.all()]

    @property
    def icon_url(self):
        # XXX probably a different static location
        return static_url('ADDON_ICON_URL') % (0, self.id, 128, self.icon_hash)

    def __unicode__(self):
        return unicode(self.url or '(no url set)')


# Maintain ElasticSearch index.
@receiver(models.signals.post_save, sender=Website,
          dispatch_uid='website_index')
def update_search_index(sender, instance, **kw):
    instance.get_indexer().index_ids([instance.id])


# Delete from ElasticSearch index on delete.
@receiver(models.signals.post_delete, sender=Website,
          dispatch_uid='website_unindex')
def delete_search_index(sender, instance, **kw):
    instance.get_indexer().unindex(instance.id)


# Save translations before saving Website instance with translated fields.
models.signals.pre_save.connect(save_signal, sender=Website,
                                dispatch_uid='website_translations')


class DBStore(object):
    def fetch_visible_website(self, pk, region=None, user=None):
        try:
            ws = Website.objects.get(pk=pk)
        except Website.DoesNotExist:
            return None
        if region is not None and region in ws.region_exclusions:
            return None
        return ws

store = DBStore()


class MemoryWebsite(object):
    def __init__(self, data):
        self.__dict__.update(data)
        self.icon_url = static_url('ADDON_ICON_URL') % (0, self.id, 128,
                                                        self.icon_hash)


class MemoryStore(object):
    """
    Verified fake for store API.
    """
    def __init__(self):
        self.sites = {}

    def fetch_visible_website(self, pk, region=None, user=None):
        ws = self.sites.get(pk)
        if ws and region not in ws.region_exclusions:
            return ws
