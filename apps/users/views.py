from functools import partial
import base64
import hashlib

from django import http
from django.conf import settings
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect
from django.contrib import auth
from django.template import Context, loader
from django.utils.datastructures import SortedDict
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.utils.encoding import smart_str
from django.utils.http import base36_to_int
from django.contrib.auth.tokens import default_token_generator

from django_browserid.auth import BrowserIDBackend

import commonware.log
import jingo
from radagast.wizard import Wizard
from ratelimit.decorators import ratelimit
from tower import ugettext as _, ugettext_lazy as _lazy
from session_csrf import anonymous_csrf, anonymous_csrf_exempt
from statsd import statsd
from mobility.decorators import mobile_template
import waffle

from access.middleware import ACLMiddleware
import amo
from amo import messages
from amo.decorators import (json_view, login_required, no_login_required,
                            permission_required, write, post_required)
from amo.forms import AbuseForm
from amo.urlresolvers import reverse
from amo.utils import send_mail
from abuse.models import send_abuse_report
from addons.models import Addon
from addons.views import BaseFilter
from addons.decorators import addon_view_factory
from access import acl
from bandwagon.models import Collection
from stats.models import Contribution
from translations.query import order_by_translation
from users.models import UserNotification
import users.notifications as notifications

from .models import UserProfile
from .signals import logged_out
from . import forms
from .utils import EmailResetCode, UnsubscribeCode
import tasks

log = commonware.log.getLogger('z.users')
addon_view = addon_view_factory(qs=Addon.objects.valid)


@login_required(redirect=False)
@json_view
def ajax(request):
    """Query for a user matching a given email."""
    email = request.GET.get('q', '').strip()
    u = get_object_or_404(UserProfile, email=email)
    return dict(id=u.id, name=u.name)


@no_login_required
def confirm(request, user_id, token):
    user = get_object_or_404(UserProfile, id=user_id)

    if not user.confirmationcode:
        return http.HttpResponseRedirect(reverse('users.login'))

    if user.confirmationcode != token:
        log.info(u"Account confirmation failed for user (%s)", user)
        messages.error(request, _('Invalid confirmation code!'))
        return http.HttpResponseRedirect(reverse('users.login'))

    user.confirmationcode = ''
    user.save()
    messages.success(request, _('Successfully verified!'))
    log.info(u"Account confirmed for user (%s)", user)
    return http.HttpResponseRedirect(reverse('users.login'))


@no_login_required
def confirm_resend(request, user_id):
    user = get_object_or_404(UserProfile, id=user_id)

    if not user.confirmationcode:
        return http.HttpResponseRedirect(reverse('users.login'))

    # Potential for flood here if someone requests a confirmationcode and then
    # re-requests confirmations.  We may need to track requests in the future.
    log.info(u"Account confirm re-requested for user (%s)", user)

    user.email_confirmation_code()

    msg = _(u'An email has been sent to your address {0} to confirm '
             'your account. Before you can log in, you have to activate '
             'your account by clicking on the link provided in this '
             'email.').format(user.email)
    messages.info(request, _('Confirmation Email Sent'), msg)

    return http.HttpResponseRedirect(reverse('users.login'))


@login_required
def delete(request):
    amouser = request.amo_user
    if request.method == 'POST':
        form = forms.UserDeleteForm(request.POST, request=request)
        if form.is_valid():
            messages.success(request, _('Profile Deleted'))
            amouser.anonymize()
            logout(request)
            form = None
            return http.HttpResponseRedirect(reverse('users.login'))
    else:
        form = forms.UserDeleteForm()

    return jingo.render(request, 'users/delete.html',
                        {'form': form, 'amouser': amouser})


@login_required
def delete_photo(request):
    u = request.amo_user

    if request.method == 'POST':
        u.picture_type = ''
        u.save()
        log.debug(u"User (%s) deleted photo" % u)
        tasks.delete_photo.delay(u.picture_path)
        messages.success(request, _('Photo Deleted'))
        return http.HttpResponseRedirect(reverse('users.edit') +
                                         '#user-profile')

    return jingo.render(request, 'users/delete_photo.html', dict(user=u))


@write
@login_required
def edit(request):
    # Don't use request.amo_user since it has too much caching.
    amouser = UserProfile.objects.get(pk=request.user.id)
    if request.method == 'POST':
        # ModelForm alters the instance you pass in.  We need to keep a copy
        # around in case we need to use it below (to email the user)
        original_email = amouser.email
        form = forms.UserEditForm(request.POST, request.FILES, request=request,
                                  instance=amouser)
        if form.is_valid():
            messages.success(request, _('Profile Updated'))
            if amouser.email != original_email:
                l = {'user': amouser,
                     'mail1': original_email,
                     'mail2': amouser.email}
                log.info(u"User (%(user)s) has requested email change from"
                          "(%(mail1)s) to (%(mail2)s)" % l)
                messages.info(request, _('Email Confirmation Sent'),
                    _(u'An email has been sent to {0} to confirm your new '
                       'email address. For the change to take effect, you '
                       'need to click on the link provided in this email. '
                       'Until then, you can keep logging in with your '
                       'current email address.').format(amouser.email))

                domain = settings.DOMAIN
                token, hash = EmailResetCode.create(amouser.id, amouser.email)
                url = "%s%s" % (settings.SITE_URL,
                                reverse('users.emailchange', args=[amouser.id,
                                                                token, hash]))
                t = loader.get_template('users/email/emailchange.ltxt')
                c = {'domain': domain, 'url': url, }
                send_mail(_(("Please confirm your email address "
                             "change at %s") % domain),
                    t.render(Context(c)), None, [amouser.email],
                    use_blacklist=False)

                # Reset the original email back.  We aren't changing their
                # address until they confirm the new one
                amouser.email = original_email
            form.save()
            return http.HttpResponseRedirect(reverse('users.edit'))
        else:

            messages.error(request, _('Errors Found'),
                                    _('There were errors in the changes '
                                      'you made. Please correct them and '
                                      'resubmit.'))
    else:
        form = forms.UserEditForm(instance=amouser)

    return jingo.render(request, 'users/edit.html',
                        {'form': form, 'amouser': amouser})


@write
@login_required
@permission_required('Admin', 'EditAnyUser')
def admin_edit(request, user_id):
    amouser = get_object_or_404(UserProfile, pk=user_id)

    if request.method == 'POST':
        form = forms.AdminUserEditForm(request.POST, request.FILES,
                                       request=request, instance=amouser)
        if form.is_valid():
            form.save()
            messages.success(request, _('Profile Updated'))
            return http.HttpResponseRedirect(reverse('zadmin.index'))
    else:
        form = forms.AdminUserEditForm(instance=amouser)
    return jingo.render(request, 'users/edit.html',
                        {'form': form, 'amouser': amouser})


def emailchange(request, user_id, token, hash):
    user = get_object_or_404(UserProfile, id=user_id)

    try:
        _uid, newemail = EmailResetCode.parse(token, hash)
    except ValueError:
        return http.HttpResponse(status=400)

    if _uid != user.id:
        # I'm calling this a warning because invalid hashes up to this point
        # could be any number of things, but this is a targeted attack from
        # one user account to another
        log.warning((u"[Tampering] Valid email reset code for UID (%s) "
                     "attempted to change email address for user (%s)")
                                                        % (_uid, user))
        return http.HttpResponse(status=400)

    user.email = newemail
    user.save()

    l = {'user': user, 'newemail': newemail}
    log.info(u"User (%(user)s) confirmed new email address (%(newemail)s)" % l)
    messages.success(request, _('Your email address was changed successfully'),
            _(u'From now on, please use {0} to log in.').format(newemail))

    return http.HttpResponseRedirect(reverse('users.edit'))


def _clean_next_url(request):
    gets = request.GET.copy()
    url = gets['to']

    if not url:
        url = settings.LOGIN_REDIRECT_URL

    # We want to not redirect outside of AMO via login/logout (also see
    # "domain" below)
    if '://' in url:
        url = '/'

    # TODO(davedash): This is a remora-ism, let's remove this after remora and
    # since all zamboni 'to' parameters will begin with '/'.
    if url and not url.startswith('/'):
        url = '/' + url

    gets['to'] = url

    domain = gets.get('domain', None)
    if domain in settings.VALID_LOGIN_REDIRECTS.keys():
        gets['to'] = "%s%s" % (settings.VALID_LOGIN_REDIRECTS[domain], url)

    request.GET = gets
    return request


def browserid_authenticate(assertion):
    """
    Verify a BrowserID login attempt. If the BrowserID assertion is
    good, but no account exists on AMO, create one.
    """
    backend = BrowserIDBackend()
    result = backend.verify(assertion, settings.SITE_URL)
    if not result:
        return None
    email = result['email']
    users = UserProfile.objects.filter(email=email)
    if len(users) == 1:
        users[0].user.backend = 'django_browserid.auth.BrowserIDBackend'
        return users[0]
    # store the username as a base64 encoded sha1 of the email address
    # this protects against data leakage because usernames are often
    # treated as public identifiers (so we can't use the email address).
    username = base64.urlsafe_b64encode(
        hashlib.sha1(email).digest()).rstrip('=')
    profile = UserProfile.objects.create(username=username, email=email)
    user = profile.create_django_user()
    profile.user.backend = 'django_browserid.auth.BrowserIDBackend'
    profile.user.save()
    profile.save()
    return profile


@anonymous_csrf
@post_required
@no_login_required
#@ratelimit(block=True, rate=settings.LOGIN_RATELIMIT_ALL_USERS)
def browserid_login(request):
    if waffle.switch_is_active('browserid-login'):
        if request.user.is_authenticated():
            return http.HttpResponse(status=200)
        with statsd.timer('auth.browserid.verify'):
            user = auth.authenticate(assertion=request.POST['assertion'])
        if user is not None:
            profile = UserProfile.objects.get(user=user)
            if profile.needs_tougher_password:
                return http.HttpResponse("", status=400)
            auth.login(request, user)
            return http.HttpResponse(status=200)
    return http.HttpResponse(status=401)


@anonymous_csrf
@mobile_template('users/{mobile/}login_modal.html')
@no_login_required
#@ratelimit(block=True, rate=settings.LOGIN_RATELIMIT_ALL_USERS)
def login_modal(request, template=None):
    return _login(request, template=template)


@anonymous_csrf
@mobile_template('users/{mobile/}login.html')
@no_login_required
#@ratelimit(block=True, rate=settings.LOGIN_RATELIMIT_ALL_USERS)
def login(request, template=None):
    return _login(request, template=template)


def _login(request, template=None, data=None, dont_redirect=False):
    data = data or {}
    # In case we need it later.  See below.
    get_copy = request.GET.copy()

    if 'to' in request.GET:
        request = _clean_next_url(request)

    if request.user.is_authenticated():
        return redirect(request.GET.get('to', settings.LOGIN_REDIRECT_URL))

    limited = getattr(request, 'limited', 'recaptcha_shown' in request.POST)
    user = None
    if 'username' in request.POST:
        try:
            # We are doing all this before we try and validate the form.
            user = UserProfile.objects.get(email=request.POST['username'])
            limited = ((user.failed_login_attempts >=
                        settings.LOGIN_RATELIMIT_USER) or limited)
            user.log_login_attempt(request, False)
        except UserProfile.DoesNotExist:
            pass

    partial_form = partial(forms.AuthenticationForm, use_recaptcha=limited)
    r = auth.views.login(request, template_name=template,
                         redirect_field_name='to',
                         authentication_form=partial_form,
                         extra_context=data)

    if isinstance(r, http.HttpResponseRedirect):
        # Django's auth.views.login has security checks to prevent someone from
        # redirecting to another domain.  Since we want to allow this in
        # certain cases, we have to make a new response object here to replace
        # the above.

        if 'domain' in request.GET:
            request.GET = get_copy
            request = _clean_next_url(request)
            r = http.HttpResponseRedirect(request.GET['to'])

        # Succsesful log in according to django.  Now we do our checks.  I do
        # the checks here instead of the form's clean() because I want to use
        # the messages framework and it's not available in the request there.
        user = request.user.get_profile()

        if user.deleted:
            logout(request)
            log.warning(u'Attempt to log in with deleted account (%s)' % user)
            messages.error(request, _('Wrong email address or password!'))
            data.update({'form': partial_form()})
            return jingo.render(request, template, data)

        if user.confirmationcode:
            logout(request)
            log.info(u'Attempt to log in with unconfirmed account (%s)' % user)
            msg1 = _(u'A link to activate your user account was sent by email '
                      'to your address {0}. You have to click it before you '
                      'can log in.').format(user.email)
            url = "%s%s" % (settings.SITE_URL,
                            reverse('users.confirm.resend', args=[user.id]))
            msg2 = _('If you did not receive the confirmation email, make '
                      'sure your email service did not mark it as "junk '
                      'mail" or "spam". If you need to, you can have us '
                      '<a href="%s">resend the confirmation message</a> '
                      'to your email address mentioned above.') % url
            messages.error(request, _('Activation Email Sent'),  msg1)
            messages.info(request, _('Having Trouble?'), msg2,
                          title_safe=True, message_safe=True)
            data.update({'form': partial_form()})
            return jingo.render(request, template, data)

        rememberme = request.POST.get('rememberme', None)
        if rememberme:
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            log.debug((u'User (%s) logged in successfully with '
                                        '"remember me" set') % user)
        else:
            user.log_login_attempt(request, True)

        if dont_redirect:
            # We're recalling the middleware to re-initialize amo_user
            ACLMiddleware().process_request(request)
            r = jingo.render(request, template, data)

    return r


def logout(request):
    # Not using get_profile() becuase user could be anonymous
    user = request.user
    if not user.is_anonymous():
        log.debug(u"User (%s) logged out" % user)

    auth.logout(request)

    if 'to' in request.GET:
        request = _clean_next_url(request)

    next = request.GET.get('to') or settings.LOGOUT_REDIRECT_URL
    response = http.HttpResponseRedirect(next)
    # Fire logged out signal so we can be decoupled from cake.
    logged_out.send(None, request=request, response=response)
    return response


def profile(request, user_id):
    user = get_object_or_404(UserProfile, id=user_id)

    # Get user's own and favorite collections, if they allowed that.
    if user.display_collections:
        own_coll = (Collection.objects.listed().filter(author=user)
                    .order_by('-created'))[:10]
    else:
        own_coll = []
    if user.display_collections_fav:
        fav_coll = (Collection.objects.listed().filter(following__user=user)
                    .order_by('-following__created'))[:10]
    else:
        fav_coll = []

    edit_any_user = acl.action_allowed(request, 'Admin', 'EditAnyUser')
    own_profile = (request.user.is_authenticated() and
                   request.amo_user.id == user.id)

    if user.is_developer:
        addons = amo.utils.paginate(request,
            user.addons_listed.order_by('-weekly_downloads'))
    else:
        addons = []

    def get_addons(reviews):
        if not reviews:
            return
        qs = Addon.objects.filter(id__in=set(r.addon_id for r in reviews))
        addons = dict((addon.id, addon) for addon in qs)
        for review in reviews:
            review.addon = addons.get(review.addon_id)
    reviews = user.reviews.transform(get_addons)

    data = {'profile': user, 'own_coll': own_coll, 'reviews': reviews,
            'fav_coll': fav_coll, 'edit_any_user': edit_any_user,
            'addons': addons, 'own_profile': own_profile}
    if not own_profile:
        data['abuse_form'] = AbuseForm(request=request)

    return jingo.render(request, 'users/profile.html', data)


@anonymous_csrf
@no_login_required
def register(request):
    if (settings.REGISTER_USER_LIMIT and
        UserProfile.objects.count() > settings.REGISTER_USER_LIMIT):
        messages.error(request, 'Sorry, no more registrations are allowed.')
        form = None

    elif request.user.is_authenticated():
        messages.info(request, _('You are already logged in to an account.'))
        form = None

    elif request.method == 'POST':

        form = forms.UserRegisterForm(request.POST)

        if form.is_valid():
            try:
                u = form.save(commit=False)
                u.set_password(form.cleaned_data['password'])
                u.generate_confirmationcode()
                u.save()
                u.create_django_user()
                log.info(u'Registered new account for user (%s)', u)

                u.email_confirmation_code()

                msg = _('Congratulations! Your user account was successfully '
                        'created.')
                messages.success(request, msg)

                msg = _(u'An email has been sent to your address {0} to '
                         'confirm your account. Before you can log in, you '
                         'have to activate your account by clicking on the '
                         'link provided in this email.').format(u.email)
                messages.info(request, _('Confirmation Email Sent'), msg)
            except IntegrityError, e:
                # I was unable to reproduce this, but I suspect it happens
                # when they POST twice quickly and the slaves don't have the
                # new info yet (total guess).  Anyway, I'm assuming the
                # first one worked properly, so this is still a success
                # case to tne end user so we just log it...
                log.error('Failed to register new user (%s): %s' % (u, e))

            return http.HttpResponseRedirect(reverse('users.login'))

        else:
            messages.error(request, _('There are errors in this form'),
                            _('Please correct them and resubmit.'))
    else:
        form = forms.UserRegisterForm()
    return jingo.render(request, 'users/register.html', {'form': form, })


@anonymous_csrf_exempt
def report_abuse(request, user_id):
    user = get_object_or_404(UserProfile, pk=user_id)
    form = AbuseForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        send_abuse_report(request, user, form.cleaned_data['text'])
        messages.success(request, _('User reported.'))
    else:
        return jingo.render(request, 'users/report_abuse_full.html',
                            {'profile': user, 'abuse_form': form, })
    return redirect(reverse('users.profile', args=[user.pk]))


@never_cache
@no_login_required
def password_reset_confirm(request, uidb36=None, token=None):
    """
    Pulled from django contrib so that we can add user into the form
    so then we can show relevant messages about the user.
    """
    assert uidb36 is not None and token is not None
    user = None
    try:
        uid_int = base36_to_int(uidb36)
        user = UserProfile.objects.get(id=uid_int)
    except (ValueError, UserProfile.DoesNotExist):
        pass

    if user is not None and default_token_generator.check_token(user, token):
        validlink = True
        if request.method == 'POST':
            form = forms.SetPasswordForm(user, request.POST)
            if form.is_valid():
                form.save()
                return redirect(reverse('django.contrib.auth.'
                                        'views.password_reset_complete'))
        else:
            form = forms.SetPasswordForm(user)
    else:
        validlink = False
        form = None

    return jingo.render(request, 'users/pwreset_confirm.html',
                        {'form': form, 'validlink': validlink})


@never_cache
def unsubscribe(request, hash=None, token=None, perm_setting=None):
    """
    Pulled from django contrib so that we can add user into the form
    so then we can show relevant messages about the user.
    """
    assert hash is not None and token is not None
    user = None

    try:
        email = UnsubscribeCode.parse(token, hash)
        user = UserProfile.objects.get(email=email)
    except (ValueError, UserProfile.DoesNotExist):
        pass

    perm_settings = []
    if user is not None:
        unsubscribed = True
        if not perm_setting:
            # TODO: make this work. nothing currently links to it, though.
            perm_settings = [l for l in notifications.NOTIFICATIONS
                             if not l.mandatory]
        else:
            perm_setting = notifications.NOTIFICATIONS_BY_SHORT[perm_setting]
            UserNotification.update_or_create(update={'enabled': False},
                    user=user, notification_id=perm_setting.id)
            perm_settings = [perm_setting]
    else:
        unsubscribed = False
        email = ''

    return jingo.render(request, 'users/unsubscribe.html',
            {'unsubscribed': unsubscribed, 'email': email,
             'perm_settings': perm_settings})


class AddonsFilter(BaseFilter):
    opts = (('price', _lazy(u'Price')),
            ('name', _lazy(u'Name')))

    def filter(self, field):
        qs = self.base_queryset
        if field == 'price':
            return qs.order_by('addonpremium__price__price')
        elif field == 'name':
            return order_by_translation(qs, 'name')


@login_required
def purchases(request, addon_id=None):
    """A list of purchases that a user has made through the marketplace."""
    if not waffle.switch_is_active('marketplace'):
        raise http.Http404
    cs = Contribution.objects.filter(user=request.amo_user,
                                     type__in=(amo.CONTRIB_PURCHASE,
                                               amo.CONTRIB_REFUND),
                                     ).order_by('created')

    if addon_id:
        cs = cs.filter(addon=addon_id)

    contributions = {}
    for c in cs:
        contributions.setdefault(c.addon_id, []).append(c)

    def is_refunded(original):
        """
        Contributions are sorted by creation date; if the last one is
        a refund, mark this addon as refunded.
        """
        txns = contributions.get(original.pk, None)
        if txns:
            return txns[-1].type == amo.CONTRIB_REFUND
        else:
            return False

    # If you are asking for a receipt for just one item, only show that.
    # Otherwise, we'll show all addons that have a contribution or are free.
    if addon_id:
        ids = [addon_id]
    else:
        free = (request.amo_user.installed_set
                       .exclude(addon__in=contributions.keys())
                       .values_list('addon_id', flat=True))
        ids = contributions.keys() + list(free)

    filter = AddonsFilter(request, Addon.objects.filter(id__in=ids),
                          key='sort', default='name')

    if addon_id and not filter.qs:
        # User has requested a receipt for an addon they don't have.
        raise http.Http404

    return jingo.render(request, 'users/purchases.html',
                        {'addons': amo.utils.paginate(request, filter.qs,
                                                      count=len(ids)),
                         'is_refunded': is_refunded,
                         'filter': filter,
                         'url_base': reverse('users.purchases'),
                         'contributions': contributions,
                         'single': bool(addon_id)})


# Start of the Support wizard all of these are accessed through the
# SupportWizard below.
def plain(request, contribution, wizard):
    # Simple view that just shows a template matching the step.
    tpl = wizard.tpl('%s.html' % wizard.step)
    return wizard.render(request, tpl, {'addon': contribution.addon,
                                        'contribution': contribution})


def support_author(request, contribution, wizard):
    addon = contribution.addon
    form = forms.ContactForm(request.POST)
    if request.method == 'POST':
        if form.is_valid():
            template = jingo.render_to_string(request,
                                wizard.tpl('emails/support-request.txt'),
                                context={'contribution': contribution,
                                         'addon': addon, 'form': form,
                                         'user': request.amo_user})
            log.info('Support request to dev. by user: %s for addon: %s' %
                     (request.amo_user.pk, addon.pk))
            # L10n: %s is the addon name.
            send_mail(_(u'New Support Request for %s' % addon.name),
                      template, request.amo_user.email,
                      [smart_str(addon.support_email)])
            return redirect(reverse('users.support',
                                    args=[contribution.pk, 'author-sent']))

    return wizard.render(request, wizard.tpl('author.html'),
                         {'addon': addon, 'form': form})


def support_mozilla(request, contribution, wizard):
    addon = contribution.addon
    form = forms.ContactForm(request.POST)
    if request.method == 'POST':
        if form.is_valid():
            template = jingo.render_to_string(request,
                                wizard.tpl('emails/support-request.txt'),
                                context={'addon': addon, 'form': form,
                                         'contribution': contribution,
                                         'user': request.amo_user})
            log.info('Support request to mozilla by user: %s for addon: %s' %
                     (request.amo_user.pk, addon.pk))
            # L10n: %s is the addon name.
            send_mail(_(u'New Support Request for %s' % addon.name),
                      template, request.amo_user.email, [settings.FLIGTAR])
            return redirect(reverse('users.support',
                                    args=[contribution.pk, 'mozilla-sent']))

    return wizard.render(request, wizard.tpl('mozilla.html'),
                         {'addon': addon, 'form': form})


def refund_request(request, contribution, wizard):
    addon = contribution.addon
    form = forms.RemoveForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            return redirect(reverse('users.support',
                                    args=[contribution.pk, 'reason']))

    return wizard.render(request, wizard.tpl('request.html'),
                         {'addon': addon, 'form': form,
                          'contribution': contribution})


def refund_reason(request, contribution, wizard):
    addon = contribution.addon
    if not 'request' in wizard.get_progress():
        return redirect(reverse('users.support',
                                args=[contribution.pk, 'request']))

    form = forms.ContactForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            # if under 30 minutes, refund
            # TODO(ashort): add in the logic for under 30 minutes.
            template = jingo.render_to_string(request,
                                wizard.tpl('emails/refund-request.txt'),
                                context={'addon': addon, 'form': form,
                                         'user': request.amo_user,
                                         'contribution': contribution})
            log.info('Refund request sent by user: %s for addon: %s' %
                     (request.amo_user.pk, addon.pk))
            # L10n: %s is the addon name.
            send_mail(_(u'New Refund Request for %s' % addon.name),
                      template, request.amo_user.email,
                      [smart_str(addon.support_email)])
            return redirect(reverse('users.support',
                                    args=[contribution.pk, 'refund-sent']))

    return wizard.render(request, wizard.tpl('refund.html'),
                         {'contribut': addon, 'form': form})


class SupportWizard(Wizard):
    title = _lazy('Support')
    steps = SortedDict((('start', plain),
                ('site', plain),
                ('resources', plain),
                ('mozilla', support_mozilla),
                ('mozilla-sent', plain),
                ('author', support_author),
                ('author-sent', plain),
                ('request', refund_request),
                ('reason', refund_reason),
                ('refund-sent', plain)))

    def tpl(self, x):
        return 'users/support/%s' % x

    @property
    def wrapper(self):
        return self.tpl('wrapper.html')

    @method_decorator(login_required)
    def dispatch(self, request, contribution_id, step='', *args, **kw):
        contribution = get_object_or_404(Contribution, pk=contribution_id)
        if contribution.user.pk != request.amo_user.pk:
            raise http.Http404
        args = [contribution] + list(args)
        return super(SupportWizard, self).dispatch(request, step, *args, **kw)
