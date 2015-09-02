import os
import socket
import StringIO
import tempfile
import time
import traceback

from django.conf import settings

import commonware.log
import elasticsearch
import requests
from cache_nuggets.lib import memoize
from PIL import Image

from lib.crypto import packaged, receipt
from lib.crypto.packaged import SigningError as PackageSigningError
from lib.crypto.receipt import SigningError
from lib.pay_server import client
from mkt.site.storage_utils import local_storage


monitor_log = commonware.log.getLogger('z.monitor')


def memcache():
    memcache = getattr(settings, 'CACHES', {}).get('default')
    memcache_results = []
    status = ''
    if memcache and 'memcache' in memcache['BACKEND']:
        hosts = memcache['LOCATION']
        using_twemproxy = False
        if not isinstance(hosts, (tuple, list)):
            hosts = [hosts]
        for host in hosts:
            ip, port = host.split(':')

            if ip == '127.0.0.1':
                using_twemproxy = True

            try:
                s = socket.socket()
                s.connect((ip, int(port)))
            except Exception, e:
                result = False
                status = 'Failed to connect to memcached (%s): %s' % (host, e)
                monitor_log.critical(status)
            else:
                result = True
            finally:
                s.close()

            memcache_results.append((ip, port, result))
        if (not using_twemproxy and len(hosts) > 1 and
                len(memcache_results) < 2):
            # If the number of requested hosts is greater than 1, but less
            # than 2 replied, raise an error.
            status = ('2+ memcache servers are required.'
                      '%s available') % len(memcache_results)
            monitor_log.warning(status)

    # If we are in debug mode, don't worry about checking for memcache.
    elif settings.DEBUG:
        return status, []

    if not memcache_results:
        status = 'Memcache is not configured'
        monitor_log.info(status)

    return status, memcache_results


def libraries():
    # Check Libraries and versions
    libraries_results = []
    status = ''
    try:
        Image.new('RGB', (16, 16)).save(StringIO.StringIO(), 'JPEG')
        libraries_results.append(('PIL+JPEG', True, 'Got it!'))
    except Exception, e:
        msg = "Failed to create a jpeg image: %s" % e
        libraries_results.append(('PIL+JPEG', False, msg))

    try:
        import M2Crypto  # NOQA
        libraries_results.append(('M2Crypto', True, 'Got it!'))
    except ImportError:
        libraries_results.append(('M2Crypto', False, 'Failed to import'))

    if settings.SPIDERMONKEY:
        if os.access(settings.SPIDERMONKEY, os.R_OK):
            libraries_results.append(('Spidermonkey is ready!', True, None))
            # TODO: see if it works?
        else:
            msg = "You said spidermonkey was at (%s)" % settings.SPIDERMONKEY
            libraries_results.append(('Spidermonkey', False, msg))
    # If settings are debug and spidermonkey is empty,
    # thorw this error.
    elif settings.DEBUG and not settings.SPIDERMONKEY:
        msg = 'SPIDERMONKEY is empty'
        libraries_results.append(('Spidermonkey', True, msg))
    else:
        msg = "Please set SPIDERMONKEY in your settings file."
        libraries_results.append(('Spidermonkey', False, msg))

    missing_libs = [l for l, s, m in libraries_results if not s]
    if missing_libs:
        status = 'missing libs: %s' % ",".join(missing_libs)
    return status, libraries_results


def elastic():
    es = elasticsearch.Elasticsearch(hosts=settings.ES_HOSTS)
    elastic_results = None
    status = ''
    try:
        health = es.cluster.health()
        if health['status'] == 'red':
            status = 'ES is red'
        elastic_results = health
    except elasticsearch.ElasticsearchException:
        monitor_log.exception('Failed to communicate with ES')
        elastic_results = {'error': traceback.format_exc()}
        status = 'traceback'

    return status, elastic_results


def path():
    # Check file paths / permissions
    rw = (settings.TMP_PATH,
          settings.NETAPP_STORAGE,
          settings.UPLOADS_PATH,
          settings.WEBAPPS_PATH,
          settings.GUARDED_WEBAPPS_PATH,
          settings.WEBAPP_ICONS_PATH,
          settings.WEBSITE_ICONS_PATH,
          settings.PREVIEWS_PATH,
          settings.REVIEWER_ATTACHMENTS_PATH,)
    r = [os.path.join(settings.ROOT, 'locale')]
    filepaths = [(path, os.R_OK | os.W_OK, "We want read + write")
                 for path in rw]
    filepaths += [(path, os.R_OK, "We want read") for path in r]
    filepath_results = []
    filepath_status = True

    for path, perms, notes in filepaths:
        path_exists = os.path.exists(path)
        path_perms = os.access(path, perms)
        filepath_status = filepath_status and path_exists and path_perms
        filepath_results.append((path, path_exists, path_perms, notes))

    key_exists = os.path.exists(settings.WEBAPPS_RECEIPT_KEY)
    key_perms = os.access(settings.WEBAPPS_RECEIPT_KEY, os.R_OK)
    filepath_status = filepath_status and key_exists and key_perms
    filepath_results.append(('settings.WEBAPPS_RECEIPT_KEY',
                             key_exists, key_perms, 'We want read'))

    status = filepath_status
    status = ''
    if not filepath_status:
        status = 'check main status page for broken perms'

    return status, filepath_results


# The signer check actually asks the signing server to sign something. Do this
# once per nagios check, once per web head might be a bit much. The memoize
# slows it down a bit, by caching the result for 15 seconds.
@memoize('monitors-signer', time=15)
def receipt_signer():
    destination = getattr(settings, 'SIGNING_SERVER', None)
    if not destination:
        return '', 'Signer is not configured.'

    # Just send some test data into the signer.
    now = int(time.time())
    not_valid = (settings.SITE_URL + '/not-valid')
    data = {'detail': not_valid, 'exp': now + 3600, 'iat': now,
            'iss': settings.SITE_URL,
            'product': {'storedata': 'id=1', 'url': u'http://not-valid.com'},
            'nbf': now, 'typ': 'purchase-receipt',
            'reissue': not_valid,
            'user': {'type': 'directed-identifier',
                     'value': u'something-not-valid'},
            'verify': not_valid
            }

    try:
        result = receipt.sign(data)
    except SigningError as err:
        msg = 'Error on signing (%s): %s' % (destination, err)
        return msg, msg

    try:
        cert, rest = receipt.crack(result)
    except Exception as err:
        msg = 'Error on cracking receipt (%s): %s' % (destination, err)
        return msg, msg

    # Check that the certs used to sign the receipts are not about to expire.
    limit = now + (60 * 60 * 24)  # One day.
    if cert['exp'] < limit:
        msg = 'Cert will expire soon (%s)' % destination
        return msg, msg

    cert_err_msg = 'Error on checking public cert (%s): %s'
    location = cert['iss']
    try:
        resp = requests.get(location, timeout=5, stream=False)
    except Exception as err:
        msg = cert_err_msg % (location, err)
        return msg, msg

    if not resp.ok:
        msg = cert_err_msg % (location, resp.reason)
        return msg, msg

    cert_json = resp.json()
    if not cert_json or 'jwk' not in cert_json:
        msg = cert_err_msg % (location, 'Not valid JSON/JWK')
        return msg, msg

    return '', 'Signer working and up to date'


# Like the receipt signer above this asks the packaged app signing
# service to sign one for us.
@memoize('monitors-package-signer', time=60)
def package_signer():
    destination = getattr(settings, 'SIGNED_APPS_SERVER', None)
    if not destination:
        return '', 'Signer is not configured.'
    app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'nagios_check_packaged_app.zip')
    signed_path = tempfile.mktemp()
    try:
        packaged.sign_app(local_storage.open(app_path), signed_path, None,
                          False, local=True)
        return '', 'Package signer working'
    except PackageSigningError, e:
        msg = 'Error on package signing (%s): %s' % (destination, e)
        return msg, msg
    finally:
        local_storage.delete(signed_path)


# Not called settings to avoid conflict with django.conf.settings.
def settings_check():
    required = ['APP_PURCHASE_KEY', 'APP_PURCHASE_TYP', 'APP_PURCHASE_AUD',
                'APP_PURCHASE_SECRET']
    for key in required:
        if not getattr(settings, key):
            msg = 'Missing required value %s' % key
            return msg, msg

    return '', 'Required settings ok'


def solitude():
    try:
        res = client.api.services.request.get()
    except Exception as err:
        return repr(err), repr(err)
    auth = res.get('authenticated', None)
    if auth != 'marketplace':
        msg = 'Solitude authenticated as: %s' % auth
        return msg, msg

    return '', 'Solitude authentication ok'
