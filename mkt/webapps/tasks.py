import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.urlresolvers import reverse
from django.template import Context, loader
from django.test.client import RequestFactory

import pytz
import requests
from celery import chord
from celery.exceptions import RetryTaskError
from celery import task
from requests.exceptions import RequestException
from tower import ugettext as _

import mkt
from lib.post_request_task.task import task as post_request_task
from mkt.abuse.models import AbuseReport
from mkt.constants.regions import RESTOFWORLD
from mkt.developers.models import ActivityLog, AppLog
from mkt.developers.tasks import _fetch_manifest, validator
from mkt.files.models import FileUpload
from mkt.reviewers.models import EscalationQueue, RereviewQueue
from mkt.site.decorators import set_task_user, use_master
from mkt.site.helpers import absolutify
from mkt.site.mail import send_mail_jinja
from mkt.site.storage_utils import (copy_stored_file, local_storage,
                                    private_storage, public_storage,
                                    walk_storage)
from mkt.site.utils import JSONEncoder, chunked
from mkt.users.models import UserProfile
from mkt.users.utils import get_task_user
from mkt.webapps.indexers import WebappIndexer
from mkt.webapps.models import Preview, Webapp
from mkt.webapps.utils import get_locale_properties


task_log = logging.getLogger('z.task')


@task
@use_master
def version_changed(webapp_id, **kw):
    update_last_updated(webapp_id)


def update_last_updated(webapp_id):
    qs = Webapp._last_updated_queries()
    if not Webapp.objects.filter(pk=webapp_id).exists():
        task_log.info(
            '[1@None] Updating last updated for %s failed, no webapp found'
            % webapp_id)
        return

    task_log.info('[1@None] Updating last updated for %s.' % webapp_id)

    res = (qs.filter(pk=webapp_id)
             .using('default')
             .values_list('id', 'last_updated'))
    if res:
        pk, t = res[0]
        Webapp.objects.filter(pk=pk).update(last_updated=t)


@task
def delete_preview_files(id, **kw):
    task_log.info('[1@None] Removing preview with id of %s.' % id)

    p = Preview(id=id)
    for f in (p.thumbnail_path, p.image_path):
        try:
            public_storage.delete(f)
        except Exception, e:
            task_log.error('Error deleting preview file (%s): %s' % (f, e))


def _get_content_hash(content):
    return 'sha256:%s' % hashlib.sha256(content).hexdigest()


def _log(webapp, message, rereview=False, exc_info=False):
    if rereview:
        message = u'(Re-review) ' + unicode(message)
    task_log.info(u'[Webapp:%s] %s' % (webapp, unicode(message)),
                  exc_info=exc_info)


@task
@use_master
def update_manifests(ids, **kw):
    retry_secs = 3600
    task_log.info('[%s@%s] Update manifests.' %
                  (len(ids), update_manifests.rate_limit))
    check_hash = kw.pop('check_hash', True)
    retries = kw.pop('retries', {})
    # Since we'll be logging the updated manifest change to the users log,
    # we'll need to log in as user.
    mkt.set_user(get_task_user())

    for id in ids:
        _update_manifest(id, check_hash, retries)
    if retries:
        try:
            update_manifests.retry(args=(retries.keys(),),
                                   kwargs={'check_hash': check_hash,
                                           'retries': retries},
                                   eta=datetime.datetime.now() +
                                   datetime.timedelta(seconds=retry_secs),
                                   max_retries=5)
        except RetryTaskError:
            _log(id, 'Retrying task in %d seconds.' % retry_secs)

    return retries


def notify_developers_of_failure(app, error_message, has_link=False):
    if (app.status not in mkt.WEBAPPS_APPROVED_STATUSES or
            RereviewQueue.objects.filter(webapp=app).exists()):
        # If the app isn't public, or has already been reviewed, we don't
        # want to send the mail.
        return

    # FIXME: how to integrate with commbadge?

    for author in app.authors.all():
        context = {
            'error_message': error_message,
            'SITE_URL': settings.SITE_URL,
            'SUPPORT_GROUP': settings.SUPPORT_GROUP,
            'has_link': has_link
        }
        to = [author.email]
        with author.activate_lang():
            # Re-fetch the app to get translations in the right language.
            context['app'] = Webapp.objects.get(pk=app.pk)

            subject = _(u'Issue with your app "{app}" on the Firefox '
                        u'Marketplace').format(**context)
            send_mail_jinja(subject,
                            'webapps/emails/update_manifest_failure.txt',
                            context, recipient_list=to)


def _update_manifest(id, check_hash, failed_fetches):
    webapp = Webapp.objects.get(pk=id)
    version = webapp.versions.latest()
    file_ = version.files.latest()

    _log(webapp, u'Fetching webapp manifest')
    if not file_:
        _log(webapp, u'Ignoring, no existing file')
        return

    # Fetch manifest, catching and logging any exception.
    try:
        content = _fetch_manifest(webapp.manifest_url)
    except Exception, e:
        msg = u'Failed to get manifest from %s. Error: %s' % (
            webapp.manifest_url, e)
        failed_fetches[id] = failed_fetches.get(id, 0) + 1
        if failed_fetches[id] == 3:
            # This is our 3rd attempt, let's send the developer(s) an email to
            # notify him of the failures.
            notify_developers_of_failure(webapp, u'Validation errors:\n' + msg)
        elif failed_fetches[id] >= 4:
            # This is our 4th attempt, we should already have notified the
            # developer(s). Let's put the app in the re-review queue.
            _log(webapp, msg, rereview=True, exc_info=True)
            if webapp.status in mkt.WEBAPPS_APPROVED_STATUSES:
                RereviewQueue.flag(webapp, mkt.LOG.REREVIEW_MANIFEST_CHANGE,
                                   msg)
            del failed_fetches[id]
        else:
            _log(webapp, msg, rereview=False, exc_info=True)
        return

    # Check hash.
    if check_hash:
        hash_ = _get_content_hash(content)
        if file_.hash == hash_:
            _log(webapp, u'Manifest the same')
            return
        _log(webapp, u'Manifest different')

    # Validate the new manifest.
    upload = FileUpload.objects.create()
    upload.add_file([content], webapp.manifest_url, len(content))

    validator(upload.pk)

    upload = FileUpload.objects.get(pk=upload.pk)
    if upload.validation:
        v8n = json.loads(upload.validation)
        if v8n['errors']:
            v8n_url = absolutify(reverse(
                'mkt.developers.upload_detail', args=[upload.uuid]))
            msg = u'Validation errors:\n'
            for m in v8n['messages']:
                if m['type'] == u'error':
                    msg += u'* %s\n' % m['message']
            msg += u'\nValidation Result:\n%s' % v8n_url
            _log(webapp, msg, rereview=True)
            if webapp.status in mkt.WEBAPPS_APPROVED_STATUSES:
                notify_developers_of_failure(webapp, msg, has_link=True)
                RereviewQueue.flag(webapp, mkt.LOG.REREVIEW_MANIFEST_CHANGE,
                                   msg)
            return
    else:
        _log(webapp,
             u'Validation for upload UUID %s has no result' % upload.uuid)

    # Get the old manifest before we overwrite it.
    new = json.loads(content)
    old = webapp.get_manifest_json(file_)

    # New manifest is different and validates, update version/file.
    try:
        webapp.manifest_updated(content, upload)
    except:
        _log(webapp, u'Failed to create version', exc_info=True)

    # Check for any name changes at root and in locales. If any were added or
    # updated, send to re-review queue.
    msg = []
    rereview = False
    # Some changes require a new call to IARC's SET_STOREFRONT_DATA.
    iarc_storefront = False

    if old and old.get('name') != new.get('name'):
        rereview = True
        iarc_storefront = True
        msg.append(u'Manifest name changed from "%s" to "%s".' % (
            old.get('name'), new.get('name')))

    new_version = webapp.versions.latest()
    # Compare developer_name between old and new version using the property
    # that fallbacks to the author name instead of using the db field directly.
    # This allows us to avoid forcing a re-review on old apps which didn't have
    # developer name in their manifest initially and upload a new version that
    # does, providing that it matches the original author name.
    if version.developer_name != new_version.developer_name:
        rereview = True
        iarc_storefront = True
        msg.append(u'Developer name changed from "%s" to "%s".'
                   % (version.developer_name, new_version.developer_name))

    # Get names in "locales" as {locale: name}.
    locale_names = get_locale_properties(new, 'name', webapp.default_locale)

    # Check changes to default_locale.
    locale_changed = webapp.update_default_locale(new.get('default_locale'))
    if locale_changed:
        msg.append(u'Default locale changed from "%s" to "%s".'
                   % locale_changed)

    # Update names
    crud = webapp.update_names(locale_names)
    if any(crud.values()):
        webapp.save()

    if crud.get('added'):
        rereview = True
        msg.append(u'Locales added: %s' % crud.get('added'))
    if crud.get('updated'):
        rereview = True
        msg.append(u'Locales updated: %s' % crud.get('updated'))

    # Check if supported_locales changed and update if so.
    webapp.update_supported_locales(manifest=new, latest=True)

    if rereview:
        msg = ' '.join(msg)
        _log(webapp, msg, rereview=True)
        if webapp.status in mkt.WEBAPPS_APPROVED_STATUSES:
            RereviewQueue.flag(webapp, mkt.LOG.REREVIEW_MANIFEST_CHANGE, msg)

    if iarc_storefront:
        webapp.set_iarc_storefront_data()


@post_request_task
@use_master
def update_cached_manifests(id, **kw):
    try:
        webapp = Webapp.objects.get(pk=id)
    except Webapp.DoesNotExist:
        _log(id, u'Webapp does not exist')
        return

    if not webapp.is_packaged:
        return

    # Rebuilds the packaged app mini manifest and stores it in cache.
    webapp.get_cached_manifest(force=True)
    _log(webapp, u'Updated cached mini manifest')


@task
@use_master
def update_supported_locales(ids, **kw):
    """
    Task intended to run via command line to update all apps' supported locales
    based on the current version.
    """
    for chunk in chunked(ids, 50):
        for app in Webapp.objects.filter(id__in=chunk):
            try:
                if app.update_supported_locales():
                    _log(app, u'Updated supported locales')
            except Exception:
                _log(app, u'Updating supported locales failed.', exc_info=True)


@post_request_task(acks_late=True)
@use_master
def index_webapps(ids, **kw):
    # DEPRECATED: call WebappIndexer.index_ids directly.
    WebappIndexer.index_ids(ids, no_delay=True)


@post_request_task(acks_late=True)
@use_master
def unindex_webapps(ids, **kw):
    # DEPRECATED: call WebappIndexer.unindexer directly.
    WebappIndexer.unindexer(ids)


@task
def dump_app(id, **kw):
    from mkt.webapps.serializers import AppSerializer
    target_dir = os.path.join(settings.DUMPED_APPS_PATH, 'apps',
                              str(id / 1000))
    target_file = os.path.join(target_dir, str(id) + '.json')

    try:
        obj = Webapp.objects.get(pk=id)
    except Webapp.DoesNotExist:
        task_log.info(u'Webapp does not exist: {0}'.format(id))
        return

    req = RequestFactory().get('/')
    req.user = AnonymousUser()
    req.REGION = RESTOFWORLD

    task_log.info('Dumping app {0} to {1}'.format(id, target_file))
    res = AppSerializer(obj, context={'request': req}).data
    with private_storage.open(target_file, 'w') as fileobj:
        json.dump(res, fileobj, cls=JSONEncoder)
    return target_file


@task(ignore_result=False)
def dump_apps(ids, **kw):
    task_log.info(u'Dumping apps {0} to {1}. [{2}]'
                  .format(ids[0], ids[-1], len(ids)))
    for id in ids:
        dump_app(id)


def rm_directory(path):
    if os.path.exists(path):
        shutil.rmtree(path)


def dump_all_apps_tasks():
    all_pks = (Webapp.objects.visible()
                             .values_list('pk', flat=True)
                             .order_by('pk'))
    return [dump_apps.si(pks) for pks in chunked(all_pks, 100)]


@task
def export_data(name=None):
    today = datetime.datetime.today().strftime('%Y-%m-%d')
    if name is None:
        name = today

    # Clean up the path where we'll store the individual json files from each
    # app dump (which are in apps/ inside DUMPED_APPS_PATH).
    path_to_cleanup = os.path.join(settings.DUMPED_APPS_PATH, 'apps')
    task_log.info('Cleaning up path {0}'.format(settings.DUMPED_APPS_PATH))
    try:
        for dirpath, dirnames, filenames in walk_storage(
                path_to_cleanup, storage=private_storage):
            for filename in filenames:
                private_storage.delete(os.path.join(dirpath, filename))
    except OSError:
        # Ignore if the directory does not exist.
        pass

    # Run all dump_apps task in parallel, and once it's done, add extra files
    # and run compression.
    chord(dump_all_apps_tasks(),
          compress_export.si(tarball_name=name, date=today)).apply_async()


def compile_extra_files(target_directory, date):
    # Put some .txt files in place. This is done locally only, it's only useful
    # before the tar command is run.
    context = Context({'date': date, 'url': settings.SITE_URL})
    extra_filenames = ['license.txt', 'readme.txt']
    for extra_filename in extra_filenames:
        template = loader.get_template('webapps/dump/apps/%s' % extra_filename)
        dst = os.path.join(target_directory, extra_filename)
        with local_storage.open(dst, 'w') as fd:
            fd.write(template.render(context))
    return extra_filenames


@task
def compress_export(tarball_name, date):
    # We need a temporary directory on the local filesystem that will contain
    # all files in order to call `tar`.
    local_source_dir = tempfile.mkdtemp()

    apps_dirpath = os.path.join(settings.DUMPED_APPS_PATH, 'apps')

    # In case apps_dirpath is empty, add a dummy file to make the apps
    # directory in the tar archive non-empty. It should not happen in prod, but
    # it's nice to have it to prevent the task from failing entirely.
    with private_storage.open(
            os.path.join(apps_dirpath, '0', '.keep'), 'w') as fd:
        fd.write('.')

    # Now, copy content from private_storage to that temp directory. We don't
    # need to worry about creating the directories locally, the storage class
    # does that for us.
    for dirpath, dirnames, filenames in walk_storage(
            apps_dirpath, storage=private_storage):
        for filename in filenames:
            src_path = os.path.join(dirpath, filename)
            dst_path = os.path.join(
                local_source_dir, 'apps', os.path.basename(dirpath), filename)
            copy_stored_file(
                src_path, dst_path, src_storage=private_storage,
                dst_storage=local_storage)

    # Also add extra files to the temp directory.
    extra_filenames = compile_extra_files(local_source_dir, date)

    # All our files are now present locally, let's generate a local filename
    # that will contain the final '.tar.gz' before it's copied over to
    # public storage.
    local_target_file = tempfile.NamedTemporaryFile(
        suffix='.tgz', prefix='dumped-apps-')

    # tar ALL the things!
    cmd = ['tar', 'czf', local_target_file.name, '-C',
           local_source_dir] + ['apps'] + extra_filenames
    task_log.info(u'Creating dump {0}'.format(local_target_file.name))
    subprocess.call(cmd)

    # Now copy the local tgz to the public storage.
    remote_target_filename = os.path.join(
        settings.DUMPED_APPS_PATH, 'tarballs', '%s.tgz' % tarball_name)
    copy_stored_file(local_target_file.name, remote_target_filename,
                     src_storage=local_storage,
                     dst_storage=public_storage)

    # Clean-up.
    local_target_file.close()
    rm_directory(local_source_dir)
    return remote_target_filename


@task(ignore_result=False)
def dump_user_installs(ids, **kw):
    task_log.info(u'Dumping user installs {0} to {1}. [{2}]'
                  .format(ids[0], ids[-1], len(ids)))

    users = (UserProfile.objects.filter(enable_recommendations=True)
             .filter(id__in=ids))
    for user in users:
        hash = user.recommendation_hash
        target_dir = os.path.join(settings.DUMPED_USERS_PATH, 'users', hash[0])
        target_file = os.path.join(target_dir, '%s.json' % hash)

        # Gather data about user.
        installed = []
        zone = pytz.timezone(settings.TIME_ZONE)
        for install in user.installed_set.all():
            try:
                app = install.webapp
            except Webapp.DoesNotExist:
                continue

            installed.append({
                'id': app.id,
                'slug': app.app_slug,
                'installed': pytz.utc.normalize(
                    zone.localize(install.created)).strftime(
                        '%Y-%m-%dT%H:%M:%S')
            })

        data = {
            'user': hash,
            'region': user.region,
            'lang': user.lang,
            'installed_apps': installed,
        }

        task_log.info('Dumping user {0} to {1}'.format(user.id, target_file))
        with private_storage.open(target_file, 'w') as fileobj:
            json.dump(data, fileobj, cls=JSONEncoder)


@task
def zip_users(*args, **kw):
    date = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    tarball_name = date

    # We need a temporary directory on the local filesystem that will contain
    # all files in order to call `tar`.
    local_source_dir = tempfile.mkdtemp()

    users_dirpath = os.path.join(settings.DUMPED_USERS_PATH, 'users')

    # In case users_dirpath is empty, add a dummy file to make the users
    # directory in the tar archive non-empty. It should not happen in prod, but
    # it's nice to have it to prevent the task from failing entirely.
    with private_storage.open(
            os.path.join(users_dirpath, '0', '.keep'), 'w') as fd:
        fd.write('.')

    # Now, copy content from private_storage to that temp directory. We don't
    # need to worry about creating the directories locally, the storage class
    # does that for us.
    for dirpath, dirnames, filenames in walk_storage(
            users_dirpath, storage=private_storage):
        for filename in filenames:
            src_path = os.path.join(dirpath, filename)
            dst_path = os.path.join(
                local_source_dir, 'users', os.path.basename(dirpath), filename)
            copy_stored_file(
                src_path, dst_path, src_storage=private_storage,
                dst_storage=local_storage)

    # Put some .txt files in place locally.
    context = Context({'date': date, 'url': settings.SITE_URL})
    extra_filenames = ['license.txt', 'readme.txt']
    for extra_filename in extra_filenames:
        template = loader.get_template('webapps/dump/users/' + extra_filename)
        dst = os.path.join(local_source_dir, extra_filename)
        with local_storage.open(dst, 'w') as fd:
            fd.write(template.render(context))

    # All our files are now present locally, let's generate a local filename
    # that will contain the final '.tar.gz' before it's copied over to
    # public storage.
    local_target_file = tempfile.NamedTemporaryFile(
        suffix='.tgz', prefix='dumped-users-')

    # tar ALL the things!
    cmd = ['tar', 'czf', local_target_file.name, '-C',
           local_source_dir] + ['users'] + extra_filenames
    task_log.info(u'Creating user dump {0}'.format(local_target_file.name))
    subprocess.call(cmd)

    # Now copy the local tgz to the public storage.
    remote_target_filename = os.path.join(
        settings.DUMPED_USERS_PATH, 'tarballs', '%s.tgz' % tarball_name)
    copy_stored_file(local_target_file.name, remote_target_filename,
                     src_storage=local_storage,
                     dst_storage=private_storage)

    # Clean-up.
    local_target_file.close()
    rm_directory(local_source_dir)
    return remote_target_filename


class PreGenAPKError(Exception):
    """
    An error encountered while trying to pre-generate an APK.
    """


@task
@use_master
def pre_generate_apk(app_id, **kw):
    app = Webapp.objects.get(pk=app_id)
    manifest_url = app.get_manifest_url()
    task_log.info(u'pre-generating APK for app {a} at {url}'
                  .format(a=app, url=manifest_url))
    if not manifest_url:
        raise PreGenAPKError(u'Webapp {w} has an empty manifest URL'
                             .format(w=app))
    try:
        res = requests.get(
            settings.PRE_GENERATE_APK_URL,
            params={'manifestUrl': manifest_url},
            headers={'User-Agent': settings.MARKETPLACE_USER_AGENT})
        res.raise_for_status()
    except RequestException, exc:
        raise PreGenAPKError(u'Error pre-generating APK for app {a} at {url}; '
                             u'generator={gen} (SSL cert ok?); '
                             u'{e.__class__.__name__}: {e}'
                             .format(a=app, url=manifest_url, e=exc,
                                     gen=settings.PRE_GENERATE_APK_URL))

    # The factory returns a binary APK blob but we don't need it.
    res.close()
    del res


@task
@use_master
def set_storefront_data(app_id, disable=False, **kw):
    """
    Call IARC's SET_STOREFRONT_DATA endpoint.
    """
    try:
        app = Webapp.with_deleted.get(pk=app_id)
    except Webapp.DoesNotExist:
        return

    app.set_iarc_storefront_data(disable=disable)


@task
def delete_logs(items, **kw):
    task_log.info('[%s@%s] Deleting logs'
                  % (len(items), delete_logs.rate_limit))
    ActivityLog.objects.filter(pk__in=items).exclude(
        action__in=mkt.LOG_KEEP).delete()


@task
@set_task_user
def find_abuse_escalations(webapp_id, **kw):
    weekago = datetime.date.today() - datetime.timedelta(days=7)
    add_to_queue = True

    for abuse in AbuseReport.recent_high_abuse_reports(1, weekago, webapp_id):
        if EscalationQueue.objects.filter(webapp=abuse.webapp).exists():
            # App is already in the queue, no need to re-add it.
            task_log.info(u'[app:%s] High abuse reports, but already '
                          u'escalated' % abuse.webapp)
            add_to_queue = False

        # We have an abuse report... has it been detected and dealt with?
        logs = (AppLog.objects.filter(
            activity_log__action=mkt.LOG.ESCALATED_HIGH_ABUSE.id,
            webapp=abuse.webapp).order_by('-created'))
        if logs:
            abuse_since_log = AbuseReport.recent_high_abuse_reports(
                1, logs[0].created, webapp_id)
            # If no abuse reports have happened since the last logged abuse
            # report, do not add to queue.
            if not abuse_since_log:
                task_log.info(u'[app:%s] High abuse reports, but none since '
                              u'last escalation' % abuse.webapp)
                continue

        # If we haven't bailed out yet, escalate this app.
        msg = u'High number of abuse reports detected'
        if add_to_queue:
<<<<<<< b2bbe4e452562a6ace455f7d624a11c2f21ffb17
            EscalationQueue.objects.create(addon=abuse.addon)
        mkt.log(mkt.LOG.ESCALATED_HIGH_ABUSE, abuse.addon,
                abuse.addon.current_version, details={'comments': msg})
        task_log.info(u'[app:%s] %s' % (abuse.addon, msg))
=======
            EscalationQueue.objects.create(webapp=abuse.webapp)
        mkt.log(mkt.LOG.ESCALATED_HIGH_ABUSE, abuse.webapp,
                abuse.webapp.current_version, details={'comments': msg})
        task_log.info(u'[app:%s] %s' % (abuse.webapp, msg))


@task
@use_master
def populate_is_offline(ids, **kw):
    for webapp in Webapp.objects.filter(pk__in=ids).iterator():
        if webapp.guess_is_offline():
            webapp.update(is_offline=True)


@task
@use_master
def adjust_categories(ids, **kw):
    NEW_APP_CATEGORIES = {
        425986: ['weather'],
        444314: ['travel', 'weather'],
        445008: ['travel', 'weather'],
        450602: ['weather'],
        455256: ['weather'],
        455660: ['travel', 'weather'],
        459364: ['weather'],
        461279: ['social', 'weather'],
        461371: ['lifestyle', 'weather'],
        462257: ['utilities', 'weather'],
        463108: ['weather'],
        466698: ['utilities', 'weather'],
        468173: ['weather'],
        470946: ['travel', 'weather'],
        482869: ['utilities', 'weather'],
        482961: ['weather'],
        496946: ['weather'],
        499699: ['weather'],
        501553: ['weather'],
        501581: ['lifestyle', 'weather'],
        501583: ['social', 'weather'],
        502171: ['weather', 'photo-video'],
        502173: ['weather', 'photo-video'],
        502685: ['weather'],
        503765: ['weather'],
        505437: ['weather'],
        506317: ['weather'],
        506543: ['weather'],
        506553: ['weather'],
        506623: ['weather', 'travel'],
        507091: ['weather'],
        507139: ['weather'],
        509150: ['weather'],
        510118: ['weather', 'utilities'],
        510334: ['weather', 'travel'],
        510726: ['weather'],
        511364: ['weather', 'utilities'],
        424184: ['food-drink', 'health-fitness'],
        439994: ['food-drink'],
        442842: ['maps-navigation', 'food-drink'],
        444056: ['lifestyle', 'food-drink'],
        444070: ['lifestyle', 'food-drink'],
        444222: ['food-drink', 'health-fitness'],
        444694: ['lifestyle', 'food-drink'],
        454558: ['food-drink', 'travel'],
        455620: ['food-drink', 'entertainment'],
        459304: ['food-drink', 'health-fitness'],
        465445: ['shopping', 'food-drink'],
        465700: ['food-drink', 'books-comics'],
        467828: ['food-drink', 'education'],
        469104: ['food-drink'],
        470145: ['food-drink', 'health-fitness'],
        471349: ['lifestyle', 'food-drink'],
        476155: ['lifestyle', 'food-drink'],
        477015: ['food-drink', 'travel'],
        497282: ['food-drink', 'health-fitness'],
        500359: ['food-drink', 'books-comics'],
        501249: ['food-drink'],
        501573: ['food-drink', 'entertainment'],
        504143: ['health-fitness', 'food-drink'],
        506111: ['health-fitness', 'food-drink'],
        506691: ['health-fitness', 'food-drink'],
        507921: ['books-comics', 'food-drink'],
        508211: ['food-drink', 'lifestyle'],
        508215: ['food-drink', 'lifestyle'],
        508990: ['food-drink', 'games'],
        506369: ['books-comics', 'humor'],
        509746: ['entertainment', 'humor'],
        509848: ['entertainment', 'humor'],
        511390: ['entertainment', 'humor'],
        511504: ['entertainment', 'humor'],
        488424: ['internet', 'reference'],
        489052: ['social', 'internet'],
        499644: ['internet', 'utilities'],
        500651: ['reference', 'internet'],
        505043: ['utilities', 'internet'],
        505407: ['utilities', 'internet'],
        505949: ['internet', 'reference'],
        508828: ['utilities', 'internet'],
        508830: ['utilities', 'internet'],
        509160: ['productivity', 'internet'],
        509606: ['productivity', 'internet'],
        509722: ['productivity', 'internet'],
        510114: ['news', 'internet'],
        364752: ['games', 'kids'],
        364941: ['games', 'kids'],
        449560: ['entertainment', 'kids'],
        466557: ['education', 'kids'],
        466811: ['photo-video', 'kids'],
        473532: ['education', 'kids'],
        473620: ['education', 'kids'],
        473865: ['education', 'kids'],
        500527: ['games', 'kids'],
        502263: ['photo-video', 'kids'],
        507497: ['education', 'kids'],
        508089: ['education', 'kids'],
        508229: ['education', 'kids'],
        508239: ['education', 'kids'],
        508247: ['education', 'kids'],
        509404: ['education', 'kids'],
        509464: ['education', 'kids'],
        509468: ['education', 'kids'],
        509470: ['education', 'kids'],
        509472: ['education', 'kids'],
        509474: ['education', 'kids'],
        509476: ['education', 'kids'],
        509478: ['education', 'kids'],
        509484: ['education', 'kids'],
        509486: ['education', 'kids'],
        509488: ['education', 'kids'],
        509490: ['education', 'kids'],
        509492: ['education', 'kids'],
        509494: ['education', 'kids'],
        509496: ['education', 'kids'],
        509498: ['education', 'kids'],
        509500: ['education', 'kids'],
        509502: ['education', 'kids'],
        509504: ['education', 'kids'],
        509508: ['education', 'kids'],
        509512: ['education', 'kids'],
        509538: ['education', 'kids'],
        509540: ['education', 'kids'],
        511502: ['games', 'kids'],
        367693: ['utilities', 'science-tech'],
        424272: ['science-tech', 'news'],
        460891: ['science-tech', 'news'],
        468278: ['science-tech', 'education'],
        468406: ['science-tech', 'education'],
        469765: ['science-tech', 'productivity'],
        480750: ['science-tech', 'education'],
        502187: ['science-tech', 'education'],
        504637: ['science-tech', 'reference'],
        506187: ['science-tech', 'utilities'],
        508672: ['news', 'science-tech'],
        510050: ['science-tech', 'education'],
        511370: ['science-tech', 'reference'],
        511376: ['science-tech', 'games'],
        512174: ['education', 'science-tech'],
        512194: ['utilities', 'science-tech'],
        377564: ['lifestyle', 'personalization'],
        451302: ['entertainment', 'personalization'],
        452888: ['personalization', 'photo-video'],
        466637: ['personalization', 'photo-video'],
        477186: ['photo-video', 'personalization'],
        477304: ['photo-video', 'personalization'],
        477314: ['photo-video', 'personalization'],
        480489: ['photo-video', 'personalization'],
        480495: ['photo-video', 'personalization'],
        481512: ['photo-video', 'personalization'],
        482162: ['music', 'personalization'],
        488892: ['social', 'personalization'],
        500037: ['entertainment', 'personalization'],
        500041: ['entertainment', 'personalization'],
        506495: ['personalization', 'music'],
        506581: ['entertainment', 'personalization'],
    }

    # Adjust apps whose categories have changed.
    for chunk in chunked(ids, 100):
        for app in Webapp.objects.filter(pk__in=chunk):
            save = False
            for k, v in CATEGORY_REDIRECTS.items():
                if k in app.categories:
                    save = True
                    app.categories.remove(k)
                    app.categories.append(v)
            if save:
                task_log.info(u'[app:{0}] Adjusted categories: {1}'
                              .format(app, app.categories))
                app.save()
    # Add apps to new categories.
    for pk, categories in NEW_APP_CATEGORIES.items():
        try:
            app = Webapp.objects.get(pk=pk)
        except Webapp.DoesNotExist:
            continue
        app.categories = categories
        app.save()
        task_log.info(u'[app:{0}] Updated app categories: {1}'
                      .format(app, categories))
>>>>>>> 正名
