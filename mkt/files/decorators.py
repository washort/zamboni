import functools
from datetime import datetime

from django import http
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.http import http_date

import commonware.log
from cache_nuggets.lib import Token

import mkt
from mkt.access import acl
from mkt.files.helpers import DiffHelper, FileViewer
from mkt.files.models import File


log = commonware.log.getLogger('z.webapps')


def allowed(request, file):
    allowed = acl.check_reviewer(request)
    if not allowed:
        try:
            webapp = file.version.webapp
        except ObjectDoesNotExist:
            raise http.Http404

        if webapp.status in mkt.REVIEWED_STATUSES:
            allowed = True
        else:
            allowed = acl.check_webapp_ownership(request, webapp, viewer=True,
                                                 dev=True)
    if not allowed:
        raise PermissionDenied
    return True


def _get_value(obj, key, value, cast=None):
    obj = getattr(obj, 'left', obj)
    key = obj.get_default(key)
    obj.select(key)
    if obj.selected:
        value = obj.selected.get(value)
        return cast(value) if cast else value


def last_modified(request, obj, key=None, **kw):
    return _get_value(obj, key, 'modified', datetime.fromtimestamp)


def etag(request, obj, key=None, **kw):
    return _get_value(obj, key, 'md5')


def webapp_file_view(func, **kwargs):
    @functools.wraps(func)
    def wrapper(request, file_id, *args, **kw):
        file_ = get_object_or_404(File, pk=file_id)
        result = allowed(request, file_)
        if result is not True:
            return result
        try:
            obj = FileViewer(file_)
        except ObjectDoesNotExist:
            raise http.Http404

        response = func(request, obj, *args, **kw)
        if obj.selected:
            response['ETag'] = '"%s"' % obj.selected.get('md5')
            response['Last-Modified'] = http_date(obj.selected.get('modified'))
        return response
    return wrapper


def compare_webapp_file_view(func, **kwargs):
    @functools.wraps(func)
    def wrapper(request, one_id, two_id, *args, **kw):
        one = get_object_or_404(File, pk=one_id)
        two = get_object_or_404(File, pk=two_id)
        for obj in [one, two]:
            result = allowed(request, obj)
            if result is not True:
                return result
        try:
            obj = DiffHelper(one, two)
        except ObjectDoesNotExist:
            raise http.Http404

        response = func(request, obj, *args, **kw)
        if obj.left.selected:
            response['ETag'] = '"%s"' % obj.left.selected.get('md5')
            response['Last-Modified'] = http_date(obj.left.selected
                                                          .get('modified'))
        return response
    return wrapper


def webapp_file_view_token(func, **kwargs):
    @functools.wraps(func)
    def wrapper(request, file_id, key, *args, **kw):
        viewer = FileViewer(get_object_or_404(File, pk=file_id))
        token = request.GET.get('token')
        if not token:
            log.error('Denying access to %s, no token.' % viewer.file.id)
            raise PermissionDenied
        if not Token.valid(token, [viewer.file.id, key]):
            log.error('Denying access to %s, token invalid.' % viewer.file.id)
            raise PermissionDenied
        return func(request, viewer, key, *args, **kw)
    return wrapper
