import base64
from datetime import datetime
import hashlib
import hmac
import os
import random
import re
import string
import time

import bcrypt

from django import forms, dispatch
from django.conf import settings
from django.contrib.auth.models import User as DjangoUser
from django.core import validators
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.template import Context, loader
from django.utils.encoding import smart_str, smart_unicode
from django.utils.functional import lazy

import caching.base as caching
import commonware.log
from tower import ugettext as _

import amo
import amo.models
from amo.urlresolvers import reverse
from translations.fields import PurifiedField
from translations.query import order_by_translation

log = commonware.log.getLogger('z.users')


def _hmac_create(userpwd, shared_key):
    """Create HMAC value based on pwd and system-local and per-user salt."""
    hmac_value = base64.b64encode(hmac.new(
        smart_str(shared_key), smart_str(userpwd), hashlib.sha512).digest())
    return hmac_value


def _bcrypt_create(hmac_value):
    """Create bcrypt hash."""
    rounds = getattr(settings, 'BCRYPT_ROUNDS', 12)
    # No need for us to create a user salt, bcrypt creates its own.
    bcrypt_value = bcrypt.hashpw(hmac_value, bcrypt.gensalt(int(rounds)))
    return bcrypt_value


def get_hexdigest(algorithm, salt, raw_password):
    return hashlib.new(algorithm, smart_str(salt + raw_password)).hexdigest()


def rand_string(length):
    return ''.join(random.choice(string.letters) for i in xrange(length))


def create_password(algorithm, raw_password):
    if algorithm == 'bcrypt':
        try:
            latest_key_id = max(settings.HMAC_KEYS.keys())
        except (AttributeError, ValueError):
            raise ValueError('settings.HMAC_KEYS must be set to a dict whose '
                             'values are secret keys.')
        shared_key = settings.HMAC_KEYS[latest_key_id]
        hmac_value = _hmac_create(raw_password, shared_key)
        return ''.join((
                'bcrypt', _bcrypt_create(hmac_value),
                '$', latest_key_id))
    else:
        salt = get_hexdigest(algorithm, rand_string(12), rand_string(12))[:64]
        hsh = get_hexdigest(algorithm, salt, raw_password)
        return '$'.join([algorithm, salt, hsh])


class UserForeignKey(models.ForeignKey):
    """
    A replacement for  models.ForeignKey('users.UserProfile').

    This field uses UserEmailField to make form fields key off the user's email
    instead of the primary key id.  We also hook up autocomplete automatically.
    """

    def __init__(self, *args, **kw):
        super(UserForeignKey, self).__init__(UserProfile, *args, **kw)

    def value_from_object(self, obj):
        return getattr(obj, self.name).email

    def formfield(self, **kw):
        defaults = {'form_class': UserEmailField}
        defaults.update(kw)
        return models.Field.formfield(self, **defaults)


class UserEmailField(forms.EmailField):

    def clean(self, value):
        if value in validators.EMPTY_VALUES:
            raise forms.ValidationError(self.error_messages['required'])
        try:
            return UserProfile.objects.get(email=value)
        except UserProfile.DoesNotExist:
            raise forms.ValidationError(_('No user with that email.'))

    def widget_attrs(self, widget):
        lazy_reverse = lazy(reverse, str)
        return {'class': 'email-autocomplete',
                'data-src': lazy_reverse('users.ajax')}


class UserProfile(amo.models.OnChangeMixin, amo.models.ModelBase):
    # nickname, firstname, & lastname are deprecated.
    nickname = models.CharField(max_length=255, default='', null=True,
                                blank=True)
    firstname = models.CharField(max_length=255, default='', blank=True)
    lastname = models.CharField(max_length=255, default='', blank=True)

    username = models.CharField(max_length=255, default='', unique=True)
    display_name = models.CharField(max_length=255, default='', null=True,
                                    blank=True)

    password = models.CharField(max_length=255, default='')
    email = models.EmailField(unique=True, null=True)

    averagerating = models.CharField(max_length=255, blank=True, null=True)
    bio = PurifiedField(short=False)
    confirmationcode = models.CharField(max_length=255, default='',
                                        blank=True)
    deleted = models.BooleanField(default=False)
    display_collections = models.BooleanField(default=False)
    display_collections_fav = models.BooleanField(default=False)
    emailhidden = models.BooleanField(default=True)
    homepage = models.URLField(max_length=255, blank=True, default='',
                               verify_exists=False)
    location = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, null=True)
    notifycompat = models.BooleanField(default=True)
    notifyevents = models.BooleanField(default=True)
    occupation = models.CharField(max_length=255, default='', blank=True)
    # This is essentially a "has_picture" flag right now
    picture_type = models.CharField(max_length=75, default='', blank=True)
    resetcode = models.CharField(max_length=255, default='', blank=True)
    resetcode_expires = models.DateTimeField(default=datetime.now, null=True,
                                             blank=True)
    sandboxshown = models.BooleanField(default=False)
    last_login_ip = models.CharField(default='', max_length=45, editable=False)
    last_login_attempt = models.DateTimeField(null=True, editable=False)
    last_login_attempt_ip = models.CharField(default='', max_length=45,
                                             editable=False)
    failed_login_attempts = models.PositiveIntegerField(default=0,
                                                        editable=False)

    user = models.ForeignKey(DjangoUser, null=True, editable=False, blank=True)

    class Meta:
        db_table = 'users'

    def __init__(self, *args, **kw):
        super(UserProfile, self).__init__(*args, **kw)
        if self.username:
            self.username = smart_unicode(self.username)

    def __unicode__(self):
        return '%s: %s' % (self.id, self.display_name or self.username)

    def get_url_path(self):
        return reverse('users.profile', args=[self.id])

    def flush_urls(self):
        urls = ['*/user/%d/' % self.id,
                self.picture_url,
                ]

        return urls

    @amo.cached_property
    def addons_listed(self):
        """Public add-ons this user is listed as author of."""
        addons = self.addons.reviewed().filter(addonuser__user=self,
                                               addonuser__listed=True)
        if settings.APP_PREVIEW:
            addons = addons.exclude(type=amo.ADDON_WEBAPP)
        return addons

    @amo.cached_property
    def apps_listed(self):
        """Public apps this user is listed as author of."""
        return self.addons.reviewed().filter(
            addonuser__user=self, addonuser__listed=True, type=amo.ADDON_WEBAPP)

    def my_addons(self, n=8):
        """Returns n addons (anything not a webapp)"""
        qs = self.addons.exclude(type=amo.ADDON_WEBAPP)
        qs = order_by_translation(qs, 'name')
        return qs[:n]

    def my_apps(self, n=8):
        """Returns n apps"""
        qs = self.addons.filter(type=amo.ADDON_WEBAPP)
        qs = order_by_translation(qs, 'name')
        return qs[:n]

    @property
    def picture_dir(self):
        split_id = re.match(r'((\d*?)(\d{0,3}?))\d{1,3}$', str(self.id))
        return os.path.join(settings.USERPICS_PATH, split_id.group(2) or '0',
                            split_id.group(1) or '0')

    @property
    def picture_path(self):
        return os.path.join(self.picture_dir, str(self.id) + '.png')

    @property
    def picture_url(self):
        if not self.picture_type:
            return settings.MEDIA_URL + '/img/zamboni/anon_user.png'
        else:
            split_id = re.match(r'((\d*?)(\d{0,3}?))\d{1,3}$', str(self.id))
            return settings.USERPICS_URL % (
                split_id.group(2) or 0, split_id.group(1) or 0, self.id,
                int(time.mktime(self.modified.timetuple())))

    @amo.cached_property
    def is_developer(self):
        return self.addonuser_set.exists()

    @amo.cached_property
    def needs_tougher_password(user):
        from access.acl import action_allowed_user
        return (action_allowed_user(user, 'Editors', '%')
                or action_allowed_user(user, 'Admin', '%'))

    @property
    def name(self):
        return smart_unicode(self.display_name or self.username)

    welcome_name = name

    @property
    def last_login(self):
        """Make UserProfile look more like auth.User."""
        # Django expects this to be non-null, so fake a login attempt.
        if not self.last_login_attempt:
            self.update(last_login_attempt=datetime.now())
        return self.last_login_attempt

    @amo.cached_property
    def reviews(self):
        """All reviews that are not dev replies."""
        return self._reviews_all.filter(reply_to=None)

    def anonymize(self):
        log.info(u"User (%s: <%s>) is being anonymized." % (self, self.email))
        self.email = None
        self.password = "sha512$Anonymous$Password"
        self.firstname = ""
        self.lastname = ""
        self.nickname = None
        self.username = "Anonymous-%s" % self.id  # Can't be null
        self.display_name = None
        self.homepage = ""
        self.deleted = True
        self.picture_type = ""
        self.save()

    def generate_confirmationcode(self):
        if not self.confirmationcode:
            self.confirmationcode = ''.join(random.sample(string.letters +
                                                          string.digits, 60))
        return self.confirmationcode

    def save(self, force_insert=False, force_update=False, using=None):
        # we have to fix stupid things that we defined poorly in remora
        if not self.resetcode_expires:
            self.resetcode_expires = datetime.now()

        # TODO POSTREMORA (maintain remora's view of user names.)
        if not self.firstname or self.lastname or self.nickname:
            self.nickname = self.name

        delete_user = None
        if self.deleted and self.user:
            delete_user = self.user
            self.user = None
            # Delete user after saving this profile.

        super(UserProfile, self).save(force_insert, force_update, using)

        if self.deleted and delete_user:
            delete_user.delete()

    def check_password(self, raw_password):
        def passwords_match(algo_and_hash, key_ver, hmac_input):
            shared_key = settings.HMAC_KEYS.get(key_ver, None)
            if not shared_key:
                log.info('Invalid shared key version "{0}"'.format(key_ver))
                return False
            bcrypt_value = algo_and_hash[6:]
            hmac_value = _hmac_create(hmac_input, shared_key)
            return bcrypt.hashpw(hmac_value, bcrypt_value) == bcrypt_value

        if self.password.startswith('bcrypt'):
            algo_and_hash, key_ver = self.password.rsplit('$', 1)
            return passwords_match(algo_and_hash, key_ver, raw_password)
        elif self.password.startswith('hh'):
            alg, salt, bc_pwd = self.password.split('$', 3)[1:]
            hsh = get_hexdigest(alg, salt, raw_password)
            algo_and_hash, key_ver = bc_pwd.rsplit('$', 1)
            if passwords_match(algo_and_hash, key_ver,
                               '$'.join((alg, salt, hsh))):
                # Re-create the hash in regular bcrypt format.
                self.set_password(raw_password)
                self.save()
                return True
        else:
            algo, salt, hsh = self.password.split('$')
            return hsh == get_hexdigest(algo, salt, raw_password)

    def set_password(self, raw_password, algorithm=None):
        if algorithm is None:
            algorithm = settings.PWD_ALGORITHM
        self.password = create_password(algorithm, raw_password)
        # Can't do CEF logging here because we don't have a request object.

    def upgrade_password_to(self, algorithm):
        if (not self.password or
            self.password.startswith(('hh', 'bcrypt'))):
            return
        algo, salt, hsh = self.password.split('$')
        bc_value = create_password('bcrypt', self.password)
        self.password = u'$'.join(['hh', algo, salt, bc_value])
        self.save()

    def email_confirmation_code(self):
        from amo.utils import send_mail
        log.debug("Sending account confirmation code for user (%s)", self)

        url = "%s%s" % (settings.SITE_URL,
                        reverse('users.confirm',
                                args=[self.id, self.confirmationcode]))
        domain = settings.DOMAIN
        t = loader.get_template('users/email/confirm.ltxt')
        c = {'domain': domain, 'url': url, }
        send_mail(_("Please confirm your email address"),
                  t.render(Context(c)), None, [self.email],
                  use_blacklist=False)

    def log_login_attempt(self, successful):
        """Log a user's login attempt"""
        self.last_login_attempt = datetime.now()
        self.last_login_attempt_ip = commonware.log.get_remote_addr()

        if successful:
            log.debug(u"User (%s) logged in successfully" % self)
            self.failed_login_attempts = 0
            self.last_login_ip = commonware.log.get_remote_addr()
        else:
            log.debug(u"User (%s) failed to log in" % self)
            if self.failed_login_attempts < 16777216:
                self.failed_login_attempts += 1

        self.save()

    def create_django_user(self):
        """Make a django.contrib.auth.User for this UserProfile."""
        # Reusing the id will make our life easier, because we can use the
        # OneToOneField as pk for Profile linked back to the auth.user
        # in the future.
        self.user = DjangoUser(id=self.pk)
        self.user.first_name = ''
        self.user.last_name = ''
        self.user.username = self.email  # f
        self.user.email = self.email
        self.user.password = self.password
        self.user.date_joined = self.created

        if self.groups.filter(rules='*:*').count():
            self.user.is_superuser = self.user.is_staff = True

        self.user.save()
        self.save()
        return self.user

    def mobile_collection(self):
        return self.special_collection(amo.COLLECTION_MOBILE,
            defaults={'slug': 'mobile', 'listed': False,
                      'name': _('My Mobile Add-ons')})

    def favorites_collection(self):
        return self.special_collection(amo.COLLECTION_FAVORITES,
            defaults={'slug': 'favorites', 'listed': False,
                      'name': _('My Favorite Add-ons')})

    def special_collection(self, type_, defaults):
        from bandwagon.models import Collection
        c, new = Collection.objects.get_or_create(
            author=self, type=type_, defaults=defaults)
        if new:
            # Do an extra query to make sure this gets transformed.
            c = Collection.objects.using('default').get(id=c.id)
        return c

    def purchase_ids(self):
        return (self.addonpurchase_set.values_list('addon_id', flat=True)
                                      .filter(type=amo.CONTRIB_PURCHASE)
                                      .order_by('pk'))

    def needs_completion(self):
        """True if user needs to complete her profile.

        A profile is incomplete without a username and display name
        since that's used for collections and other display items.
        """
        return not self.username or not self.display_name

    def get_preapproval(self):
        """
        Returns the pre approval object for this user, or None if it does
        not exist
        """
        try:
            return self.preapprovaluser
        except ObjectDoesNotExist:
            pass


@dispatch.receiver(models.signals.post_save, sender=UserProfile,
                   dispatch_uid='user.post_save')
def user_post_save(sender, instance, **kw):
    if not kw.get('raw'):
        from . import tasks
        tasks.index_users.delay([instance.id])


@dispatch.receiver(models.signals.post_delete, sender=UserProfile,
                   dispatch_uid='user.post_delete')
def user_post_delete(sender, instance, **kw):
    if not kw.get('raw'):
        from . import tasks
        tasks.unindex_users.delay([instance.id])


class UserNotification(amo.models.ModelBase):
    user = models.ForeignKey(UserProfile, related_name='notifications')
    notification_id = models.IntegerField()
    enabled = models.BooleanField(default=False)

    class Meta:
        db_table = 'users_notifications'

    @staticmethod
    def update_or_create(update={}, **kwargs):
        rows = UserNotification.objects.filter(**kwargs).update(**update)
        if not rows:
            update.update(dict(**kwargs))
            UserNotification.objects.create(**update)


class RequestUserManager(amo.models.ManagerBase):

    def get_query_set(self):
        qs = super(RequestUserManager, self).get_query_set()
        return qs.transform(RequestUser.transformer)


class RequestUser(UserProfile):
    """
    A RequestUser has extra attributes we don't care about for normal users.
    """

    objects = RequestUserManager()

    def __init__(self, *args, **kw):
        super(RequestUser, self).__init__(*args, **kw)
        self.mobile_addons = []
        self.favorite_addons = []
        self.watching = []

    class Meta:
        proxy = True

    @staticmethod
    def transformer(users):
        # We don't want to cache these things on every UserProfile; they're
        # only used by a user attached to a request.
        if not users:
            return
        from bandwagon.models import CollectionAddon, CollectionWatcher
        SPECIAL = amo.COLLECTION_SPECIAL_SLUGS.keys()
        user = users[0]
        qs = CollectionAddon.objects.filter(
            collection__author=user, collection__type__in=SPECIAL)
        addons = dict((type_, []) for type_ in SPECIAL)
        for addon, ctype in qs.values_list('addon', 'collection__type'):
            addons[ctype].append(addon)
        user.mobile_addons = addons[amo.COLLECTION_MOBILE]
        user.favorite_addons = addons[amo.COLLECTION_FAVORITES]
        user.watching = list((CollectionWatcher.objects.filter(user=user)
                             .values_list('collection', flat=True)))
        # Touch this @cached_property so the answer is cached with the object.
        user.is_developer

    def _cache_keys(self):
        # Add UserProfile.cache_key so RequestUser gets invalidated when the
        # UserProfile is changed.
        keys = super(RequestUser, self)._cache_keys()
        return keys + (UserProfile(id=self.id).cache_key,)


class BlacklistedUsername(amo.models.ModelBase):
    """Blacklisted user usernames."""
    username = models.CharField(max_length=255, unique=True, default='')

    class Meta:
        db_table = 'users_blacklistedusername'

    def __unicode__(self):
        return self.username

    @classmethod
    def blocked(cls, username):
        """Check to see if a username is in the (cached) blacklist."""
        qs = cls.objects.all()
        f = lambda: [u.lower() for u in qs.values_list('username', flat=True)]
        blacklist = caching.cached_with(qs, f, 'blocked')
        return username.lower() in blacklist


class BlacklistedEmailDomain(amo.models.ModelBase):
    """Blacklisted user e-mail domains."""
    domain = models.CharField(max_length=255, unique=True, default='',
                              blank=False)

    def __unicode__(self):
        return self.domain

    @classmethod
    def blocked(cls, domain):
        qs = cls.objects.all()
        f = lambda: list(qs.values_list('domain', flat=True))
        blacklist = caching.cached_with(qs, f, 'blocked')
        # because there isn't a good way to know if the domain is
        # "example.com" or "example.co.jp", we'll re-construct it...
        # so if it's "bad.example.co.jp", the following check the
        # values in ['bad.example.co.jp', 'example.co.jp', 'co.jp']
        x = domain.lower().split('.')
        for d in ['.'.join(x[y:]) for y in range(len(x) - 1)]:
            if d in blacklist:
                return True


class PersonaAuthor(unicode):
    """Stub user until the persona authors get imported."""

    @property
    def id(self):
        """I don't want to change code depending on PersonaAuthor.id, so I'm
        just hardcoding 0.  The only code using this is flush_urls."""
        return 0

    @property
    def name(self):
        return self

    display_name = name


class BlacklistedPassword(amo.models.ModelBase):
    """Blacklisted passwords"""
    password = models.CharField(max_length=255, unique=True, blank=False)

    def __unicode__(self):
        return self.password

    @classmethod
    def blocked(cls, password):
        return cls.objects.filter(password=password)


class UserHistory(amo.models.ModelBase):
    email = models.EmailField()
    user = models.ForeignKey(UserProfile, related_name='history')

    class Meta:
        db_table = 'users_history'
        ordering = ('-created',)


@UserProfile.on_change
def watch_email(old_attr={}, new_attr={}, instance=None,
                sender=None, **kw):
    new_email, old_email = new_attr.get('email'), old_attr.get('email')
    if old_email and new_email != old_email:
        log.debug('Creating user history for user: %s' % instance.pk)
        UserHistory.objects.create(email=old_email, user_id=instance.pk)
