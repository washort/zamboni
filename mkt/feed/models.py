import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

import amo.models
from amo.models import SlugField
from addons.models import Category, Preview
from translations.fields import PurifiedField, save_signal

import mkt.carriers
import mkt.regions
from mkt.collections.fields import ColorField
from mkt.collections.models import Collection
from mkt.constants.feed import FEEDAPP_TYPES
from mkt.ratings.validators import validate_rating
from mkt.webapps.models import Webapp


class FeedApp(amo.models.ModelBase):
    """
    Thin wrapper around the Webapp class that allows single apps to be featured
    on the feed.
    """
    app = models.ForeignKey(Webapp)
    feedapp_type = models.CharField(choices=FEEDAPP_TYPES, max_length=30)
    description = PurifiedField()
    slug = SlugField(max_length=30, unique=True)
    background_color = ColorField(null=True)
    has_image = models.BooleanField(default=False)

    # Optionally linked to a Preview (screenshot or video).
    preview = models.ForeignKey(Preview, null=True, blank=True)

    # Optionally linked to a pull quote.
    pullquote_rating = models.PositiveSmallIntegerField(null=True, blank=True,
        validators=[validate_rating])
    pullquote_text = PurifiedField(null=True)
    pullquote_attribution = PurifiedField(null=True)

    image_hash = models.CharField(default=None, max_length=8, null=True,
                                  blank=True)

    class Meta:
        db_table = 'mkt_feed_app'

    def clean(self):
        """
        Require `pullquote_text` if `pullquote_rating` or
        `pullquote_attribution` are set.
        """
        if not self.pullquote_text and (self.pullquote_rating or
                                        self.pullquote_attribution):
            raise ValidationError('Pullquote text required if rating or '
                                  'attribution is defined.')
        super(FeedApp, self).clean()

    def image_path(self):
        return os.path.join(settings.FEATURED_APP_BG_PATH,
                            str(self.pk / 1000),
                            'featured_app_%s.png' % (self.pk,))


class FeedItem(amo.models.ModelBase):
    """
    Allows objects from multiple models to be hung off the feed.
    """
    category = models.ForeignKey(Category, null=True, blank=True)
    region = models.PositiveIntegerField(
        default=None, null=True, blank=True, db_index=True,
        choices=mkt.regions.REGIONS_CHOICES_ID)
    carrier = models.IntegerField(default=None, null=True, blank=True,
                                  choices=mkt.carriers.CARRIER_CHOICES,
                                  db_index=True)

    # Types of objects that may be contained by a feed item.
    app = models.ForeignKey(FeedApp, null=True)
    collection = models.ForeignKey(Collection, null=True)

    class Meta:
        db_table = 'mkt_feed_item'


# Save translations when saving a Feedapp instance.
models.signals.pre_save.connect(save_signal, sender=FeedApp,
                                dispatch_uid='feedapp_translations')
