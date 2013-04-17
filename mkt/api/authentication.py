import hashlib
import hmac
import json

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User

import commonware.log
from tastypie import http
from tastypie.authentication import Authentication
from tastypie.authorization import Authorization

from access import acl
from access.middleware import ACLMiddleware
from mkt.api.middleware import APIPinningMiddleware

from mkt.api.models import Access, Token, ACCESS_TOKEN
from mkt.api.oauth import OAuthServer

log = commonware.log.getLogger('z.api')


class OwnerAuthorization(Authorization):

    def is_authorized(self, request, object=None):
        # There is no object being passed, so we'll assume it's ok
        if not object:
            return True
        # There is no request user or no user on the object.
        if not request.amo_user:
            return False

        return self.check_owner(request, object)

    def check_owner(self, request, object):
        if not object.user:
            return False
        # If the user on the object and the amo_user match, we are golden.
        return object.user.pk == request.amo_user.pk


class AppOwnerAuthorization(OwnerAuthorization):

    def check_owner(self, request, object):
        # If the user on the object and the amo_user match, we are golden.
        return object.authors.filter(user__id=request.amo_user.pk)


class PermissionAuthorization(Authorization):

    def __init__(self, app, action, *args, **kw):
        self.app, self.action = app, action

    def is_authorized(self, request, object=None):
        if acl.action_allowed(request, self.app, self.action):
            log.info('Permission authorization failed')
            return True
        return False


class OAuthError(RuntimeError):
    def __init__(self, message='OAuth error occured.'):
        self.message = message


errors = {
    'headers': 'Error with OAuth headers',
    'roles': 'Cannot be a user with roles.',
    'terms': 'Terms of service not accepted.',
}


class OAuthAuthentication(Authentication):
    """
    This is based on https://github.com/amrox/django-tastypie-two-legged-oauth
    with permission.
    """

    def __init__(self, realm='API'):
        self.realm = realm

    def _error(self, reason):
        return http.HttpUnauthorized(content=json.dumps({'reason':
                                                         errors[reason]}))

    def is_authenticated(self, request, **kwargs):
        if not settings.SITE_URL:
            raise ValueError('SITE_URL is not specified')

        auth_header_value = request.META.get('HTTP_AUTHORIZATION')
        if (not auth_header_value and
            'oauth_token' not in request.META['QUERY_STRING']):
            self.user = AnonymousUser()
            return self._error('headers')
        auth_header = {'Authorization': auth_header_value}

        method = getattr(request, 'signed_method', request.method)
        oauth = OAuthServer()
        if ('oauth_token' in request.META['QUERY_STRING'] or
            'oauth_token' in auth_header_value):
            # This is 3-legged OAuth.
            try:
                valid, oauth_request = oauth.verify_request(
                    request.build_absolute_uri(),
                    method, headers=auth_header,
                    require_resource_owner=True)
            except ValueError:
                return False
            if not valid:
                log.error(u'Cannot find APIAccess token with that key: %s'
                          % oauth.attempted_key)
                return self._error('headers')
            try:
                request.user = Token.objects.get(
                    token_type=ACCESS_TOKEN,
                    key=oauth_request.resource_owner_key).user
            except Token.DoesNotExist:
                request.user = AnonymousUser()
        else:
            # This is 2-legged OAuth.
            try:
                valid, oauth_request = oauth.verify_request(
                    request.build_absolute_uri(),
                    method, headers=auth_header,
                    require_resource_owner=False)
            except ValueError:
                return False
            if not valid:
                log.error(u'Cannot find APIAccess token with that key: %s'
                          % oauth.attempted_key)
                return self._error('headers')
            try:
                request.user = Access.objects.get(
                    key=oauth_request.client_key).user
            except Access.DoesNotExist:
                request.user = AnonymousUser()
        ACLMiddleware().process_request(request)
        # We've just become authenticated, time to run the pinning middleware
        # again.
        #
        # TODO: I'd like to see the OAuth authentication move to middleware.
        request.API = True  # We can be pretty sure we are in the API.
        APIPinningMiddleware().process_request(request)

        # Do not allow access without agreeing to the dev agreement.
        if not request.amo_user.read_dev_agreement:
            log.info(u'Attempt to use API without dev agreement: %s'
                     % request.amo_user.pk)
            return self._error('terms')

        # But you cannot have one of these roles.
        denied_groups = set(['Admins'])
        roles = set(request.amo_user.groups.values_list('name', flat=True))
        if roles and roles.intersection(denied_groups):
            log.info(u'Attempt to use API with denied role, user: %s'
                     % request.amo_user.pk)
            return self._error('roles')

        return True


class OptionalOAuthAuthentication(OAuthAuthentication):
    """
    Like OAuthAuthentication, but doesn't require there to be
    authentication headers. If no headers are provided, just continue
    as an anonymous user.
    """

    def is_authenticated(self, request, **kw):
        auth_header_value = request.META.get('HTTP_AUTHORIZATION', None)
        if (not auth_header_value and
            'oauth_token' not in request.META['QUERY_STRING']):
            request.user = AnonymousUser()
            return True

        return (super(OptionalOAuthAuthentication, self)
                .is_authenticated(request, **kw))


class SharedSecretAuthentication(Authentication):

    def is_authenticated(self, request, **kwargs):
        auth = request.GET.get('_user')
        if not auth:
            log.info('API request made without shared-secret auth token')
            return False
        try:
            email, hm, unique_id = str(auth).split(',')
            consumer_id = hashlib.sha1(
                email + settings.SECRET_KEY).hexdigest()
            matches = hmac.new(unique_id + settings.SECRET_KEY,
                               consumer_id, hashlib.sha512).hexdigest() == hm
            if matches:
                try:
                    request.user = User.objects.get(email=email)
                except User.DoesNotExist:
                    log.info('Auth token matches absent user (%s)' % email)
                    return False

                ACLMiddleware().process_request(request)
            else:
                log.info('Shared-secret auth token does not match')

            return matches
        except Exception, e:
            log.info('Bad shared-secret auth data: %s (%s)', auth, e)
            return False
