import os
import shutil

from django.conf import settings
from django.core.files.storage import default_storage as storage

import mock
from nose.tools import eq_

import amo
import amo.tests
from amo.tests.test_helpers import get_image_path
from addons.models import Addon
from files.helpers import copyfileobj
from mkt.developers import forms


class TestPreviewForm(amo.tests.TestCase):
    fixtures = ['base/addon_3615']

    def setUp(self):
        self.addon = Addon.objects.get(pk=3615)
        self.dest = os.path.join(settings.TMP_PATH, 'preview')
        if not os.path.exists(self.dest):
            os.makedirs(self.dest)

    @mock.patch('amo.models.ModelBase.update')
    def test_preview_modified(self, update_mock):
        name = 'transparent.png'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        shutil.copyfile(get_image_path(name), os.path.join(self.dest, name))
        assert form.is_valid()
        form.save(self.addon)
        assert update_mock.called

    def test_preview_size(self):
        name = 'non-animated.gif'
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': name,
                                  'position': 1})
        with storage.open(os.path.join(self.dest, name), 'wb') as f:
            copyfileobj(open(get_image_path(name)), f)
        assert form.is_valid()
        form.save(self.addon)
        eq_(self.addon.previews.all()[0].sizes,
            {u'image': [250, 297], u'thumbnail': [126, 150]})

    def check_file_type(self, type_):
        form = forms.PreviewForm({'caption': 'test', 'upload_hash': type_,
                                  'position': 1})
        assert form.is_valid()
        form.save(self.addon)
        return self.addon.previews.all()[0].filetype

    def test_preview_good_file_type(self):
        eq_(self.check_file_type('x.video-webm'), 'video/webm')

    def test_preview_other_file_type(self):
        eq_(self.check_file_type('x'), 'image/png')

    def test_preview_bad_file_type(self):
        eq_(self.check_file_type('x.foo'), 'image/png')


class TestPaypalSetupForm(amo.tests.TestCase):

    def test_email_not_required(self):
        data = {'business_account': 'no',
                'email': ''}
        assert forms.PaypalSetupForm(data=data).is_valid()

    def test_email_required(self):
        data = {'business_account': 'yes',
                'email': ''}
        assert not forms.PaypalSetupForm(data=data).is_valid()

    def test_email_gotten(self):
        data = {'business_account': 'yes',
                'email': 'foo@bar.com'}
        assert forms.PaypalSetupForm(data=data).is_valid()

    def test_email_malformed(self):
        data = {'business_account': 'yes',
                'email': 'foo'}
        assert not forms.PaypalSetupForm(data=data).is_valid()


class TestInappConfigForm(amo.tests.TestCase):
    fixtures = ['webapps/337141-steamcube']

    def setUp(self):
        self.addon = Addon.objects.get(pk=337141)

    def submit(self, **params):
        data = {'postback_url': '/p',
                'chargeback_url': '/c',
                'is_https': False}
        data.update(params)
        fm = forms.InappConfigForm(data=data)
        cfg = fm.save(commit=False)
        cfg.addon = self.addon
        cfg.save()
        return cfg

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', True)
    def test_cannot_override_https(self):
        cfg = self.submit(is_https=False)
        # This should be True because you cannot configure https.
        eq_(cfg.is_https, True)

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', False)
    def test_can_override_https(self):
        cfg = self.submit(is_https=False)
        eq_(cfg.is_https, False)
