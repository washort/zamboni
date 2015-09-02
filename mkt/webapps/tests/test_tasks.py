# -*- coding: utf-8 -*-
import datetime
import hashlib
import json
import os
import tarfile
from copy import deepcopy
from tempfile import mkdtemp

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.core.urlresolvers import reverse

import mock
from nose.tools import eq_, ok_
from requests.exceptions import RequestException

import mkt
import mkt.site.tests
from mkt.developers.models import ActivityLog
from mkt.files.models import File, FileUpload
from mkt.reviewers.models import RereviewQueue
from mkt.site.fixtures import fixture
from mkt.site.helpers import absolutify
from mkt.site.storage_utils import private_storage, public_storage
from mkt.site.utils import app_factory
from mkt.users.models import UserProfile
from mkt.versions.models import Version
from mkt.webapps.cron import dump_user_installs_cron
from mkt.webapps.models import WebappUser, Webapp
from mkt.webapps.tasks import (dump_app, export_data,
                               notify_developers_of_failure, pre_generate_apk,
                               PreGenAPKError, rm_directory, update_manifests)


original = {
    "version": "0.1",
    "default_locale": "en-US",
    "name": "MozillaBall",
    "description": "Exciting Open Web development action!",
    "icons": {
        "32": "http://test.com/icon-32.png",
        "48": "http://test.com/icon-48.png",
        "128": "http://test.com/icon-128.png"
    },
    "installs_allowed_from": [
        "*",
    ],
    "locales": {
        "de": {
            "name": "Mozilla Kugel"
        },
        "fr": {
            "description": "Testing name-less locale"
        }
    }
}


new = {
    "version": "1.0",
    "default_locale": "en-US",
    "name": "MozillaBall",
    "description": "Exciting Open Web development action!",
    "icons": {
        "32": "http://test.com/icon-32.png",
        "48": "http://test.com/icon-48.png",
        "128": "http://test.com/icon-128.png"
    },
    "installs_allowed_from": [
        "*",
    ],
    "locales": {
        "de": {
            "name": "Mozilla Kugel"
        },
        "fr": {
            "description": "Testing name-less locale"
        }
    },
    "developer": {
        "name": "Mozilla",
        "url": "http://www.mozilla.org/"
    }
}


ohash = ('sha256:'
         'fc11fba25f251d64343a7e8da4dfd812a57a121e61eb53c78c567536ab39b10d')
nhash = ('sha256:'
         '409fbe87dca5a4a7937e3dea27b69cb3a3d68caf39151585aef0c7ab46d8ee1e')


class TestUpdateManifest(mkt.site.tests.TestCase):
    fixtures = fixture('user_2519', 'user_999')

    def setUp(self):
        UserProfile.objects.get_or_create(id=settings.TASK_USER_ID)

        # Not using app factory since it creates translations with an invalid
        # locale of "en-us".
        self.webapp = Webapp.objects.create()
        self.version = Version.objects.create(webapp=self.webapp,
                                              _developer_name='Mozilla')
        self.file = File.objects.create(
            version=self.version, hash=ohash, status=mkt.STATUS_PUBLIC,
            filename='%s-%s' % (self.webapp.id, self.version.id))

        self.webapp.name = {
            'en-US': 'MozillaBall',
            'de': 'Mozilla Kugel',
        }
        self.webapp.status = mkt.STATUS_PUBLIC
        self.webapp.manifest_url = 'http://nowhere.allizom.org/manifest.webapp'
        self.webapp.save()

        self.webapp.update_version()

        self.webapp.webappuser_set.create(user_id=999)

        with public_storage.open(self.file.file_path, 'w') as fh:
            fh.write(json.dumps(original))

        # This is the hash to set the get_content_hash to, for showing
        # that the webapp has been updated.
        self._hash = nhash
        # Let's use deepcopy so nested dicts are copied as new objects.
        self.new = deepcopy(new)

        self.content_type = 'application/x-web-app-manifest+json'

        req_patcher = mock.patch('mkt.developers.tasks.requests.get')
        self.req_mock = req_patcher.start()
        self.addCleanup(req_patcher.stop)

        self.response_mock = mock.Mock(status_code=200)
        self.response_mock.iter_content.return_value = mock.Mock(
            next=self._data)
        self.response_mock.headers = {'content-type': self.content_type}
        self.req_mock.return_value = self.response_mock

        validator_patcher = mock.patch('mkt.webapps.tasks.validator')
        self.validator = validator_patcher.start()
        self.addCleanup(validator_patcher.stop)
        self.validator.return_value = {}

    @mock.patch('mkt.webapps.tasks._get_content_hash')
    def _run(self, _get_content_hash, **kw):
        # Will run the task and will act depending upon how you've set hash.
        _get_content_hash.return_value = self._hash
        update_manifests(ids=(self.webapp.pk,), **kw)

    def _data(self):
        return json.dumps(self.new)

    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    @mock.patch('mkt.webapps.models.copy_stored_file')
    def test_new_version_not_created(self, _copy_stored_file, _manifest_json):
        # Test that update_manifest doesn't create multiple versions/files.
        eq_(self.webapp.versions.count(), 1)
        old_version = self.webapp.current_version
        old_file = self.webapp.get_latest_file()
        self._run()

        app = self.webapp.reload()
        version = app.current_version
        file_ = app.get_latest_file()

        # Test that our new version looks good.
        eq_(app.versions.count(), 1)
        eq_(version, old_version, 'Version created')
        eq_(file_, old_file, 'File created')

        path = FileUpload.objects.all()[0].path
        _copy_stored_file.assert_called_with(
            path, os.path.join(version.path_prefix, file_.filename),
            src_storage=private_storage, dst_storage=private_storage)
        _manifest_json.assert_called_with(file_)

    @mock.patch('mkt.developers.tasks.validator', lambda uid, **kw: None)
    def test_version_updated(self):
        self._run()
        self.new['version'] = '1.1'

        self._hash = 'foo'
        self._run()

        app = self.webapp.reload()
        eq_(app.versions.latest().version, '1.1')

    def test_not_log(self):
        self._hash = ohash
        self._run()
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 0)

    def test_log(self):
        self._run()
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 1)

    @mock.patch('mkt.webapps.tasks._update_manifest')
    def test_pending(self, mock_):
        self.webapp.update(status=mkt.STATUS_PENDING)
        call_command('process_addons', task='update_manifests')
        assert mock_.called

    def test_pending_updates(self):
        """
        PENDING apps don't have a current version. This test makes sure
        everything still works in this case.
        """
        self.webapp.update(status=mkt.STATUS_PENDING)
        self._run()
        eq_(self.webapp.latest_version.reload().version, '1.0')

    @mock.patch('mkt.webapps.tasks._update_manifest')
    def test_approved(self, mock_):
        self.webapp.update(status=mkt.STATUS_APPROVED)
        call_command('process_addons', task='update_manifests')
        assert mock_.called

    @mock.patch('mkt.webapps.tasks._update_manifest')
    def test_ignore_disabled(self, mock_):
        self.webapp.update(status=mkt.STATUS_DISABLED)
        call_command('process_addons', task='update_manifests')
        assert not mock_.called

    @mock.patch('mkt.webapps.tasks._update_manifest')
    def test_ignore_packaged(self, mock_):
        self.webapp.update(is_packaged=True)
        call_command('process_addons', task='update_manifests')
        assert not mock_.called

    @mock.patch('mkt.webapps.tasks._update_manifest')
    def test_get_webapp(self, mock_):
        eq_(self.webapp.status, mkt.STATUS_PUBLIC)
        call_command('process_addons', task='update_manifests')
        assert mock_.called

    @mock.patch('mkt.webapps.tasks._fetch_manifest')
    @mock.patch('mkt.webapps.tasks.update_manifests.retry')
    def test_update_manifest(self, retry, fetch):
        fetch.return_value = '{}'
        update_manifests(ids=(self.webapp.pk,))
        assert not retry.called

    @mock.patch('mkt.webapps.tasks._fetch_manifest')
    @mock.patch('mkt.webapps.tasks.update_manifests.retry')
    def test_manifest_fetch_fail(self, retry, fetch):
        later = datetime.datetime.now() + datetime.timedelta(seconds=3600)
        fetch.side_effect = RuntimeError
        update_manifests(ids=(self.webapp.pk,))
        retry.assert_called()
        # Not using assert_called_with b/c eta is a datetime.
        eq_(retry.call_args[1]['args'], ([self.webapp.pk],))
        eq_(retry.call_args[1]['kwargs'], {'check_hash': True,
                                           'retries': {self.webapp.pk: 1}})
        self.assertCloseToNow(retry.call_args[1]['eta'], later)
        eq_(retry.call_args[1]['max_retries'], 5)
        eq_(len(mail.outbox), 0)

    def test_notify_failure_lang(self):
        user1 = UserProfile.objects.get(pk=999)
        user2 = UserProfile.objects.get(pk=2519)
        WebappUser.objects.create(webapp=self.webapp, user=user2)
        user1.update(lang='de')
        user2.update(lang='en')
        notify_developers_of_failure(self.webapp, 'blah')
        eq_(len(mail.outbox), 2)
        ok_(u'Mozilla Kugel' in mail.outbox[0].subject)
        ok_(u'MozillaBall' in mail.outbox[1].subject)

    def test_notify_failure_with_rereview(self):
        RereviewQueue.flag(self.webapp, mkt.LOG.REREVIEW_MANIFEST_CHANGE,
                           'This app is flagged!')
        notify_developers_of_failure(self.webapp, 'blah')
        eq_(len(mail.outbox), 0)

    def test_notify_failure_not_public(self):
        self.webapp.update(status=mkt.STATUS_PENDING)
        notify_developers_of_failure(self.webapp, 'blah')
        eq_(len(mail.outbox), 0)

    @mock.patch('mkt.webapps.tasks._fetch_manifest')
    @mock.patch('mkt.webapps.tasks.update_manifests.retry')
    def test_manifest_fetch_3rd_attempt(self, retry, fetch):
        fetch.side_effect = RuntimeError
        update_manifests(ids=(self.webapp.pk,), retries={self.webapp.pk: 2})
        # We already tried twice before, this is the 3rd attempt,
        # We should notify the developer that something is wrong.
        eq_(len(mail.outbox), 1)
        msg = mail.outbox[0]
        ok_(msg.subject.startswith('Issue with your app'))
        expected = u'Failed to get manifest from %s' % self.webapp.manifest_url
        ok_(expected in msg.body)
        ok_(settings.SUPPORT_GROUP in msg.body)

        # We should have scheduled a retry.
        assert retry.called

        # We shouldn't have put the app in the rereview queue yet.
        assert not RereviewQueue.objects.filter(webapp=self.webapp).exists()

    @mock.patch('mkt.webapps.tasks._fetch_manifest')
    @mock.patch('mkt.webapps.tasks.update_manifests.retry')
    @mock.patch('mkt.webapps.tasks.notify_developers_of_failure')
    def test_manifest_fetch_4th_attempt(self, notify, retry, fetch):
        fetch.side_effect = RuntimeError
        update_manifests(ids=(self.webapp.pk,), retries={self.webapp.pk: 3})
        # We already tried 3 times before, this is the 4th and last attempt,
        # we shouldn't retry anymore, instead we should just add the app to
        # the re-review queue. We shouldn't notify the developer either at this
        # step, it should have been done before already.
        assert not notify.called
        assert not retry.called
        assert RereviewQueue.objects.filter(webapp=self.webapp).exists()

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    def test_manifest_validation_failure(self, _iarc):
        # We are already mocking validator, but this test needs to make sure
        # it actually saves our custom validation result, so add that.
        def side_effect(upload_id, **kwargs):
            upload = FileUpload.objects.get(pk=upload_id)
            upload.validation = json.dumps(validation_results)
            upload.save()

        validation_results = {
            'errors': 1,
            'messages': [{
                'context': None,
                'uid': 'whatever',
                'column': None,
                'id': ['webapp', 'detect_webapp', 'parse_error'],
                'file': '',
                'tier': 1,
                'message': 'JSON Parse Error',
                'type': 'error',
                'line': None,
                'description': 'The webapp extension could not be parsed due '
                               'to a syntax error in the JSON.'
            }]
        }
        self.validator.side_effect = side_effect

        eq_(RereviewQueue.objects.count(), 0)

        self._run()

        eq_(RereviewQueue.objects.count(), 1)
        eq_(len(mail.outbox), 1)
        msg = mail.outbox[0]
        upload = FileUpload.objects.get()
        validation_url = absolutify(reverse(
            'mkt.developers.upload_detail', args=[upload.uuid]))
        ok_(msg.subject.startswith('Issue with your app'))
        ok_(validation_results['messages'][0]['message'] in msg.body)
        ok_(validation_url in msg.body)
        ok_(not _iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_name_change_rereview(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        self.new['name'] = 'Mozilla Ball Ultimate Edition'

        eq_(RereviewQueue.objects.count(), 0)
        self._run()
        eq_(RereviewQueue.objects.count(), 1)
        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)
        ok_(_iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_locale_name_add_rereview(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        self.new['locales'] = {'es': {'name': 'eso'}}

        eq_(RereviewQueue.objects.count(), 0)
        self._run()
        eq_(RereviewQueue.objects.count(), 1)
        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)
        log = ActivityLog.objects.filter(
            action=mkt.LOG.REREVIEW_MANIFEST_CHANGE.id)[0]
        eq_(log.details.get('comments'),
            u'Locales added: "eso" (es).')
        ok_(not _iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_locale_name_change_rereview(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        self.new['locales'] = {'de': {'name': 'Bippity Bop'}}

        eq_(RereviewQueue.objects.count(), 0)
        self._run()
        eq_(RereviewQueue.objects.count(), 1)
        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)
        log = ActivityLog.objects.filter(
            action=mkt.LOG.REREVIEW_MANIFEST_CHANGE.id)[0]
        eq_(log.details.get('comments'),
            u'Locales updated: "Mozilla Kugel" -> "Bippity Bop" (de).')
        ok_(not _iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_default_locale_change(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        self.new['name'] = u'Mozilla Balón'
        self.new['default_locale'] = 'es'
        self.new['locales'] = {'en-US': {'name': 'MozillaBall'}}

        eq_(RereviewQueue.objects.count(), 0)
        self._run()
        eq_(RereviewQueue.objects.count(), 1)
        eq_(self.webapp.reload().default_locale, 'es')
        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)
        log = ActivityLog.objects.filter(
            action=mkt.LOG.REREVIEW_MANIFEST_CHANGE.id)[0]
        eq_(log.details.get('comments'),
            u'Manifest name changed from "MozillaBall" to "Mozilla Balón". '
            u'Default locale changed from "en-US" to "es". '
            u'Locales added: "Mozilla Balón" (es).')
        ok_(_iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_locale_name_removal_no_rereview(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        # Note: Not using `del` b/c copy doesn't copy nested structures.
        self.new['locales'] = {
            'fr': {'description': 'Testing name-less locale'}
        }

        eq_(RereviewQueue.objects.count(), 0)
        self._run()
        eq_(RereviewQueue.objects.count(), 0)
        # Log for manifest update.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 1)
        ok_(not _iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_force_rereview(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with name change.
        self.new['name'] = 'Mozilla Ball Ultimate Edition'

        # We're setting the hash to the same value.
        self.file.update(hash=nhash)

        eq_(RereviewQueue.objects.count(), 0)
        self._run(check_hash=False)

        # We should still get a rereview since we bypassed the manifest check.
        eq_(RereviewQueue.objects.count(), 1)

        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)

        ok_(_iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_support_locales_change(self, _manifest, _iarc):
        """
        Test both PUBLIC and PENDING to catch apps w/o `current_version`.
        """
        for status in (mkt.STATUS_PUBLIC, mkt.STATUS_PENDING):
            self.webapp.update(status=status)

            # Mock original manifest file lookup.
            _manifest.return_value = original
            # Mock new manifest with name change.
            self.new['locales'].update({'es': {'name': u'Mozilla Balón'}})

            self._run()
            ver = self.version.reload()
            eq_(ver.supported_locales, 'de,es,fr')
            ok_(not _iarc.called)

    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_manifest_support_developer_change(self, _manifest, _iarc):
        # Mock original manifest file lookup.
        _manifest.return_value = original
        # Mock new manifest with developer name change.
        self.new['developer']['name'] = 'Allizom'

        self._run()
        ver = self.version.reload()
        eq_(ver.developer_name, 'Allizom')

        # We should get a re-review because of the developer name change.
        eq_(RereviewQueue.objects.count(), 1)
        # 2 logs: 1 for manifest update, 1 for re-review trigger.
        eq_(ActivityLog.objects.for_apps([self.webapp]).count(), 2)

        ok_(_iarc.called)


class TestDumpApps(mkt.site.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def test_dump_app(self):
        path = dump_app(337141)
        with private_storage.open(path, 'r') as fd:
            result = json.load(fd)
        eq_(result['id'], 337141)


class TestDumpUserInstalls(mkt.site.tests.TestCase):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestDumpUserInstalls, self).setUp()
        # Create a user install.
        self.app = Webapp.objects.get(pk=337141)
        self.user = UserProfile.objects.get(pk=2519)
        self.app.installed.create(user=self.user)
        self.export_directory = mkdtemp()
        self.hash = hashlib.sha256('%s%s' % (str(self.user.pk),
                                             settings.SECRET_KEY)).hexdigest()
        self.path = os.path.join('users', self.hash[0], '%s.json' % self.hash)
        self.tarfile = None
        self.tarfile_file = None

    def tearDown(self):
        rm_directory(self.export_directory)
        if self.tarfile:
            self.tarfile.close()
        if self.tarfile_file:
            self.tarfile_file.close()
        super(TestDumpUserInstalls, self).tearDown()

    def _test_export_is_created(self):
        expected_files = [
            'license.txt',
            'readme.txt',
        ]
        actual_files = self.tarfile.getnames()
        for expected_file in expected_files:
            assert expected_file in actual_files, expected_file

    def create_export(self):
        date = datetime.datetime.today().strftime('%Y-%m-%d')
        with self.settings(DUMPED_USERS_PATH=self.export_directory):
            dump_user_installs_cron()
        tarball_path = os.path.join(self.export_directory,
                                    'tarballs',
                                    date + '.tgz')
        self.tarfile_file = private_storage.open(tarball_path)
        self.tarfile = tarfile.open(fileobj=self.tarfile_file)
        return self.tarfile

    def dump_and_load(self):
        self.create_export()
        self._test_export_is_created()
        return json.load(self.tarfile.extractfile(self.path))

    def test_dump_user_installs(self):
        data = self.dump_and_load()
        eq_(data['user'], self.hash)
        eq_(data['region'], self.user.region)
        eq_(data['lang'], self.user.lang)
        installed = data['installed_apps'][0]
        eq_(installed['id'], self.app.id)
        eq_(installed['slug'], self.app.app_slug)
        self.assertCloseToNow(
            datetime.datetime.strptime(installed['installed'],
                                       '%Y-%m-%dT%H:%M:%S'),
            datetime.datetime.utcnow())

    def test_dump_exludes_deleted(self):
        """We can't recommend deleted apps, so don't include them."""
        app = app_factory()
        app.installed.create(user=self.user)
        app.delete()

        data = self.dump_and_load()
        eq_(len(data['installed_apps']), 1)
        installed = data['installed_apps'][0]
        eq_(installed['id'], self.app.id)

    def test_dump_recommendation_opt_out(self):
        self.user.update(enable_recommendations=False)
        with self.assertRaises(KeyError):
            # File shouldn't exist b/c we didn't write it.
            self.dump_and_load()


<<<<<<< b2bbe4e452562a6ace455f7d624a11c2f21ffb17
=======
class TestFixMissingIcons(mkt.site.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        self.app = Webapp.objects.get(pk=337141)

    @mock.patch('mkt.webapps.tasks._fix_missing_icons')
    def test_pending(self, mock_):
        self.app.update(status=mkt.STATUS_PENDING)
        call_command('process_addons', task='fix_missing_icons')
        assert mock_.called

    @mock.patch('mkt.webapps.tasks._fix_missing_icons')
    def test_approved(self, mock_):
        self.app.update(status=mkt.STATUS_APPROVED)
        call_command('process_addons', task='fix_missing_icons')
        assert mock_.called

    @mock.patch('mkt.webapps.tasks._fix_missing_icons')
    def test_ignore_disabled(self, mock_):
        self.app.update(status=mkt.STATUS_DISABLED)
        call_command('process_addons', task='fix_missing_icons')
        assert not mock_.called

    @mock.patch('mkt.webapps.tasks.fetch_icon')
    @mock.patch('mkt.webapps.tasks._log')
    @mock.patch('mkt.webapps.tasks.public_storage.exists')
    def test_for_missing_size(self, exists, _log, fetch_icon):
        exists.return_value = False
        call_command('process_addons', task='fix_missing_icons')

        # We are checking two sizes, but since the 64 has already failed for
        # this app, we should only have called exists() once, and we should
        # never have logged that the 128 icon is missing.
        eq_(exists.call_count, 1)
        assert _log.any_call(337141, 'Webapp is missing icon size 64')
        assert _log.any_call(337141, 'Webapp is missing icon size 128')
        assert fetch_icon.called


class TestRegenerateIconsAndThumbnails(mkt.site.tests.TestCase):
    fixtures = fixture('webapp_337141')

    @mock.patch('mkt.webapps.tasks.resize_preview.delay')
    def test_command(self, resize_preview):
        preview = Preview.objects.create(filetype='image/png',
                                         webapp_id=337141)
        call_command('process_addons', task='regenerate_icons_and_thumbnails')

        resize_preview.assert_called_once_with(preview.image_path, preview.pk,
                                               generate_image=False)


>>>>>>> 正名
@mock.patch('mkt.webapps.tasks.requests')
class TestPreGenAPKs(mkt.site.tests.WebappTestCase):

    def setUp(self):
        super(TestPreGenAPKs, self).setUp()
        self.manifest_url = u'http://some-âpp.net/manifest.webapp'
        self.app.update(manifest_url=self.manifest_url)

    def test_get(self, req):
        res = mock.Mock()
        req.get.return_value = res
        pre_generate_apk.delay(self.app.id)
        assert req.get.called, 'APK requested from factory'
        assert req.get.mock_calls[0].startswith(
            settings.PRE_GENERATE_APK_URL), req.get.mock_calls
        assert res.raise_for_status.called, 'raise on bad status codes'

    def test_get_packaged(self, req):
        self.app.update(manifest_url=None, is_packaged=True)
        # Make sure this doesn't raise an exception.
        pre_generate_apk.delay(self.app.id)
        assert req.get.called, 'APK requested from factory'

    def test_no_manifest(self, req):
        self.app.update(manifest_url=None)
        with self.assertRaises(PreGenAPKError):
            pre_generate_apk.delay(self.app.id)

    def test_error_getting(self, req):
        req.get.side_effect = RequestException
        with self.assertRaises(PreGenAPKError):
            pre_generate_apk.delay(self.app.id)


class TestExportData(mkt.site.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        self.export_directory = mkdtemp()
        self.existing_tarball = os.path.join(
            self.export_directory, 'tarballs', '2004-08-15')
        with public_storage.open(self.existing_tarball, 'w') as fd:
            fd.write('.')
        self.app_path = 'apps/337/337141.json'
        self.tarfile_file = None
        self.tarfile = None

    def tearDown(self):
        rm_directory(self.export_directory)
        if self.tarfile:
            self.tarfile.close()
        if self.tarfile_file:
            self.tarfile_file.close()
        super(TestExportData, self).tearDown()

    def create_export(self, name):
        with self.settings(DUMPED_APPS_PATH=self.export_directory):
            export_data(name=name)
        tarball_path = os.path.join(self.export_directory,
                                    'tarballs',
                                    name + '.tgz')
        self.tarfile_file = public_storage.open(tarball_path)
        self.tarfile = tarfile.open(fileobj=self.tarfile_file)
        return self.tarfile

    def test_export_is_created(self):
        expected_files = [
            self.app_path,
            'license.txt',
            'readme.txt',
        ]
        tarball = self.create_export('tarball-name')
        actual_files = tarball.getnames()
        for expected_file in expected_files:
            assert expected_file in actual_files, expected_file

        # Make sure we didn't touch old tarballs by accident.
        assert public_storage.exists(self.existing_tarball)

    @mock.patch('mkt.webapps.tasks.dump_app')
    def test_not_public(self, dump_app):
        app = Webapp.objects.get(pk=337141)
        app.update(status=mkt.STATUS_PENDING)
        self.create_export('tarball-name')
        assert not dump_app.called

<<<<<<< b2bbe4e452562a6ace455f7d624a11c2f21ffb17
    def test_removed(self):
        # At least one public app must exist for dump_apps to run.
        app_factory(name='second app', status=mkt.STATUS_PUBLIC)
        app_path = os.path.join(self.export_directory, self.app_path)
        app = Webapp.objects.get(pk=337141)
        app.update(status=mkt.STATUS_PUBLIC)
        self.create_export('tarball-name')
        assert private_storage.exists(app_path)
=======
    @mock.patch('mkt.webapps.tasks.index_webapps')
    def test_ignore_restricted(self, _mock):
        """Set up exclusions and verify they still exist after the call."""
        self.app.geodata.update(restricted=True)
        self.app.webappexcludedregion.create(region=mkt.regions.PER.id)
        self.app.webappexcludedregion.create(region=mkt.regions.FRA.id)
        fix_excluded_regions([self.app.pk])
        self.assertSetEqual(self.app.get_excluded_region_ids(),
                            [mkt.regions.PER.id, mkt.regions.FRA.id])
        eq_(self.app.webappexcludedregion.count(), 2)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    def test_free_iarc_excluded(self, _mock):
        # Set a few exclusions that shouldn't survive.
        self.app.webappexcludedregion.create(region=mkt.regions.PER.id)
        self.app.webappexcludedregion.create(region=mkt.regions.FRA.id)
        # Set IARC settings to influence region exclusions.
        self.app.geodata.update(region_de_iarc_exclude=True,
                                region_br_iarc_exclude=True)
        fix_excluded_regions([self.app.pk])
        self.assertSetEqual(self.app.get_excluded_region_ids(),
                            [mkt.regions.DEU.id, mkt.regions.BRA.id])
        eq_(self.app.webappexcludedregion.count(), 0)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    def test_paid(self, _mock):
        self.make_premium(self.app)
        fix_excluded_regions([self.app.pk])
        # There are no exclusions at all, because the payments fall back
        # to rest of the world.
        self.assertSetEqual(self.app.get_excluded_region_ids(), [])
        eq_(self.app.webappexcludedregion.count(), 0)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    def test_paid_and_worldwide(self, _mock):
        self.make_premium(self.app)
        fix_excluded_regions([self.app.pk])
        self.app.webappexcludedregion.create(region=mkt.regions.RESTOFWORLD.id)
        # All the other countries are excluded, but not the US because they
        # choose to exclude the rest of the world.
        excluded = set(mkt.regions.ALL_REGION_IDS) - set([mkt.regions.USA.id])
        self.assertSetEqual(self.app.get_excluded_region_ids(), excluded)
        eq_(self.app.webappexcludedregion.count(), 1)

    @mock.patch('mkt.webapps.tasks.index_webapps')
    def test_free_special_excluded(self, _mock):
        for region in mkt.regions.SPECIAL_REGION_IDS:
            self.app.webappexcludedregion.create(region=region)
        fix_excluded_regions([self.app.pk])
        self.assertSetEqual(self.app.get_excluded_region_ids(),
                            mkt.regions.SPECIAL_REGION_IDS)
        eq_(self.app.webappexcludedregion.count(),
            len(mkt.regions.SPECIAL_REGION_IDS))


class TestAdjustCategories(mkt.site.tests.TestCase):
    fixtures = fixture('webapp_337141')
>>>>>>> 正名

        app.update(status=mkt.STATUS_PENDING)
        self.create_export('tarball-name')
        assert not private_storage.exists(app_path)

    @mock.patch('mkt.webapps.tasks.dump_app')
    def test_public(self, dump_app):
        self.create_export('tarball-name')
        assert dump_app.called
