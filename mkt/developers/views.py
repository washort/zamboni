import json
import os
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime

from django import forms as django_forms
from django import http
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

import commonware.log
import jingo
import jinja2
import waffle
from session_csrf import anonymous_csrf, anonymous_csrf_exempt
from tower import ugettext as _, ugettext_lazy as _lazy
from waffle.decorators import waffle_switch

import amo
import amo.utils
from access import acl
from addons import forms as addon_forms
from addons.decorators import addon_view
from addons.models import Addon, AddonUser
from addons.views import BaseFilter
from amo import messages
from amo.decorators import (any_permission_required, json_view, login_required,
                            post_required)
from amo.urlresolvers import reverse
from amo.utils import escape_all
from devhub.models import AppLog
from files.models import File, FileUpload
from files.utils import parse_addon
from stats.models import Contribution
from users.models import UserProfile
from users.views import _login
from versions.models import Version

from lib.iarc.utils import get_iarc_app_title

from mkt.api.models import Access, generate
from mkt.comm.utils import create_comm_note
from mkt.constants import comm
from mkt.developers.decorators import dev_required
from mkt.developers.forms import (APIConsumerForm, AppFormBasic,
                                  AppFormDetails, AppFormMedia, AppFormSupport,
                                  AppFormTechnical, AppVersionForm,
                                  CategoryForm, IARCGetAppInfoForm,
                                  NewPackagedAppForm, PreloadTestPlanForm,
                                  PreviewFormSet, TransactionFilterForm,
                                  trap_duplicate)
from mkt.developers.models import PreloadTestPlan
from mkt.developers.tasks import run_validator, save_test_plan
from mkt.developers.utils import check_upload
from mkt.submit.forms import AppFeaturesForm, NewWebappVersionForm
from mkt.webapps.models import IARCInfo, Webapp
from mkt.webapps.tasks import _update_manifest, update_manifests

from . import forms, tasks


log = commonware.log.getLogger('z.devhub')


# We use a session cookie to make sure people see the dev agreement.
DEV_AGREEMENT_COOKIE = 'yes-I-read-the-dev-agreement'


class AddonFilter(BaseFilter):
    opts = (('name', _lazy(u'Name')),
            ('updated', _lazy(u'Updated')),
            ('created', _lazy(u'Created')),
            ('popular', _lazy(u'Downloads')),
            ('rating', _lazy(u'Rating')))


class AppFilter(BaseFilter):
    opts = (('name', _lazy(u'Name')),
            ('created', _lazy(u'Created')))


def addon_listing(request, default='name', webapp=False):
    """Set up the queryset and filtering for addon listing for Dashboard."""
    Filter = AppFilter if webapp else AddonFilter
    addons = UserProfile.objects.get(pk=request.user.id).addons
    if webapp:
        qs = Webapp.objects.filter(id__in=addons.filter(type=amo.ADDON_WEBAPP))
        model = Webapp
    else:
        qs = addons.exclude(type=amo.ADDON_WEBAPP)
        model = Addon
    filter = Filter(request, qs, 'sort', default, model=model)
    return filter.qs, filter


@anonymous_csrf
def login(request, template=None):
    return _login(request, template='developers/login.html')


def home(request):
    return index(request)


@login_required
def index(request):
    # This is a temporary redirect.
    return redirect('mkt.developers.apps')


@login_required
def dashboard(request, webapp=False):
    addons, filter = addon_listing(request, webapp=webapp)
    addons = amo.utils.paginate(request, addons, per_page=10)
    data = dict(addons=addons, sorting=filter.field, filter=filter,
                sort_opts=filter.opts, webapp=webapp)
    return jingo.render(request, 'developers/apps/dashboard.html', data)


@dev_required(webapp=True, staff=True)
def edit(request, addon_id, addon, webapp=False):
    data = {
        'page': 'edit',
        'addon': addon,
        'webapp': webapp,
        'valid_slug': addon.app_slug,
        'tags': addon.tags.not_blacklisted().values_list('tag_text',
                                                         flat=True),
        'previews': addon.get_previews(),
        'version': addon.current_version or addon.latest_version
    }
    if not addon.is_packaged and data['version']:
        data['feature_list'] = [unicode(f) for f in
                                data['version'].features.to_list()]
    if acl.action_allowed(request, 'Apps', 'Configure'):
        data['admin_settings_form'] = forms.AdminSettingsForm(instance=addon,
                                                              request=request)
    return jingo.render(request, 'developers/apps/edit.html', data)


@dev_required(owner_for_post=True, webapp=True)
@post_required
def delete(request, addon_id, addon, webapp=False):
    # Database deletes only allowed for free or incomplete addons.
    if not addon.can_be_deleted():
        msg = _('Paid apps cannot be deleted. Disable this app instead.')
        messages.error(request, msg)
        return redirect(addon.get_dev_url('versions'))

    # TODO: Force the user to re-auth with BrowserID (this DeleteForm doesn't
    # ask the user for his password)
    form = forms.DeleteForm(request)
    if form.is_valid():
        reason = form.cleaned_data.get('reason', '')
        addon.delete(msg='Removed via devhub', reason=reason)
        messages.success(request, _('App deleted.'))
        # Preserve query-string parameters if we were directed from Dashboard.
        return redirect(request.GET.get('to') or
                        reverse('mkt.developers.apps'))
    else:
        msg = _('Password was incorrect.  App was not deleted.')
        messages.error(request, msg)
        return redirect(addon.get_dev_url('versions'))


@dev_required
@post_required
def enable(request, addon_id, addon):
    addon.update(disabled_by_user=False)
    amo.log(amo.LOG.USER_ENABLE, addon)
    return redirect(addon.get_dev_url('versions'))


@dev_required
@post_required
def disable(request, addon_id, addon):
    addon.update(disabled_by_user=True)
    amo.log(amo.LOG.USER_DISABLE, addon)
    return redirect(addon.get_dev_url('versions'))


@dev_required
@post_required
def publicise(request, addon_id, addon):
    if addon.status == amo.STATUS_PUBLIC_WAITING:
        addon.update(status=amo.STATUS_PUBLIC)
        File.objects.filter(
            version__addon=addon, status=amo.STATUS_PUBLIC_WAITING).update(
                status=amo.STATUS_PUBLIC)
        amo.log(amo.LOG.CHANGE_STATUS, addon.get_status_display(), addon)
        # Call update_version, so various other bits of data update.
        addon.update_version()
        # Call to update names and locales if changed.
        addon.update_name_from_package_manifest()
        addon.update_supported_locales()

        if waffle.switch_is_active('iarc'):
            addon.set_iarc_storefront_data()

    return redirect(addon.get_dev_url('versions'))


@dev_required(webapp=True)
def status(request, addon_id, addon, webapp=False):
    form = forms.AppAppealForm(request.POST, product=addon)
    upload_form = NewWebappVersionForm(request.POST or None, is_packaged=True,
                                       addon=addon, request=request)

    if request.method == 'POST':
        if 'resubmit-app' in request.POST and form.is_valid():
            form.save()
            create_comm_note(addon, addon.current_version,
                             request.amo_user, form.data['notes'],
                             note_type=comm.RESUBMISSION)

            messages.success(request, _('App successfully resubmitted.'))
            return redirect(addon.get_dev_url('versions'))

        elif 'upload-version' in request.POST and upload_form.is_valid():
            mobile_only = (addon.latest_version and
                           addon.latest_version.features.has_qhd)

            ver = Version.from_upload(upload_form.cleaned_data['upload'],
                                      addon, [amo.PLATFORM_ALL])

            # Update addon status now that the new version was saved.
            addon.update_status()

            res = run_validator(ver.all_files[0].file_path)
            validation_result = json.loads(res)

            # Set all detected features as True and save them.
            keys = ['has_%s' % feature.lower()
                    for feature in validation_result['feature_profile']]
            data = defaultdict.fromkeys(keys, True)

            # Set "Smartphone-Sized Displays" if it's a mobile-only app.
            qhd_devices = (set((amo.DEVICE_GAIA,)),
                           set((amo.DEVICE_MOBILE,)),
                           set((amo.DEVICE_GAIA, amo.DEVICE_MOBILE,)))
            if set(addon.device_types) in qhd_devices or mobile_only:
                data['has_qhd'] = True

            # Update feature profile for this version.
            ver.features.update(**data)

            messages.success(request, _('New version successfully added.'))
            log.info('[Webapp:%s] New version created id=%s from upload: %s'
                     % (addon, ver.pk, upload_form.cleaned_data['upload']))
            return redirect(addon.get_dev_url('versions.edit', args=[ver.pk]))

    ctx = {'addon': addon, 'webapp': webapp, 'form': form,
           'upload_form': upload_form}

    # Used in the delete version modal.
    if addon.is_packaged:
        versions = addon.versions.values('id', 'version')
        version_strings = dict((v['id'], v) for v in versions)
        version_strings['num'] = len(versions)
        ctx['version_strings'] = json.dumps(version_strings)

    if addon.status == amo.STATUS_REJECTED:
        try:
            entry = (AppLog.objects
                     .filter(addon=addon,
                             activity_log__action=amo.LOG.REJECT_VERSION.id)
                     .order_by('-created'))[0]
        except IndexError:
            entry = None
        # This contains the rejection reason and timestamp.
        ctx['rejection'] = entry and entry.activity_log

    if waffle.switch_is_active('preload-apps'):
        test_plan = PreloadTestPlan.objects.filter(
            addon=addon, status=amo.STATUS_PUBLIC)
        if test_plan.exists():
            test_plan = test_plan[0]
            if (test_plan.last_submission <
                settings.PREINSTALL_TEST_PLAN_LATEST):
                ctx['outdated_test_plan'] = True
            ctx['next_step_suffix'] = 'submit'
        else:
            ctx['next_step_suffix'] = 'home'
        ctx['test_plan'] = test_plan

    return jingo.render(request, 'developers/apps/status.html', ctx)


def _submission_msgs():
    return {
        'complete': _('Congratulations, your app submission is now complete '
                      'and will be reviewed shortly!'),
        'content_ratings_saved': _('Content ratings successfully saved.'),
    }


def _ratings_success_msg(app, old_status, old_modified):
    """
    Ratings can be created via IARC pinging our API.
    Thus we can't display a success message via the standard POST/req/res.
    To workaround, we stored app's rating's `modified` from edit page.
    When hitting back to the ratings summary page, calc what msg to show.

    old_status -- app status during ratings edit page.
    old_modified -- rating modified datetime during ratings edit page.
    """
    if old_status != app.status:
        # App just created a rating to go pending, show 'app now pending'.
        return _submission_msgs()['complete']

    elif old_modified != app.last_rated_time():
        # App create/update rating, but was already pending/public, show 'ok'.
        return _submission_msgs()['content_ratings_saved']


@waffle_switch('iarc')
@dev_required
def content_ratings(request, addon_id, addon):
    if not addon.is_rated():
        return redirect(addon.get_dev_url('ratings_edit'))

    # Use _ratings_success_msg to display success message.
    session = request.session
    if 'ratings_edit' in session and addon.id in session['ratings_edit']:
        prev_state = session['ratings_edit'][addon.id]
        msg = _ratings_success_msg(addon, prev_state['app_status'],
                                   prev_state['rating_modified'])
        messages.success(request, msg) if msg else None
        del session['ratings_edit'][addon.id]  # Clear msg so not shown again.
        request.session.modified = True

    return jingo.render(
        request, 'developers/apps/ratings/ratings_summary.html', {
            'addon': addon
        })


@waffle_switch('iarc')
@dev_required
def content_ratings_edit(request, addon_id, addon):
    initial = {}
    try:
        app_info = addon.iarc_info
        initial['submission_id'] = app_info.submission_id
        initial['security_code'] = app_info.security_code
    except IARCInfo.DoesNotExist:
        pass

    form = IARCGetAppInfoForm(data=request.POST or None, initial=initial)

    if request.method == 'POST' and form.is_valid():
        try:
            form.save(addon)
            return redirect(addon.get_dev_url('ratings'))
        except django_forms.ValidationError:
            pass  # Fall through to show the form error.

    # Save some information for _ratings_success_msg.
    if not 'ratings_edit' in request.session:
        request.session['ratings_edit'] = {}
    request.session['ratings_edit'][addon.id] = {
        'app_status': addon.status,
        'rating_modified': addon.last_rated_time()
    }
    request.session.modified = True

    return jingo.render(
        request, 'developers/apps/ratings/ratings_edit.html', {
            'addon': addon,
            'app_name': get_iarc_app_title(addon),
            'form': form,
            # Force double escaping of developer name. If this has HTML
            # entities we want the escaped version to be passed to IARC.
            # See bug 962362.
            'company': jinja2.escape(unicode(
                jinja2.escape(addon.latest_version.developer_name))),
            'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })


@waffle_switch('preload-apps')
@dev_required
def preload_home(request, addon_id, addon):
    """
    Gives information on the preload process, links to test plan template.
    """
    return jingo.render(request, 'developers/apps/preload/home.html', {
        'addon': addon
    })


@waffle_switch('preload-apps')
@dev_required(owner_for_post=True, webapp=True)
def preload_submit(request, addon_id, addon, webapp):
    if request.method == 'POST':
        form = PreloadTestPlanForm(request.POST, request.FILES)
        if form.is_valid():
            # Save test plan file.
            test_plan = request.FILES['test_plan']
            # Figure the type to save it as (cleaned as pdf/xls from the form).
            if test_plan.content_type == 'application/pdf':
                filename = 'test_plan_%s.pdf'
            else:
                filename = 'test_plan_%s.xls'
            # Timestamp.
            filename = filename % str(time.time()).split('.')[0]
            save_test_plan(request.FILES['test_plan'], filename, addon)

            # Log test plan.
            PreloadTestPlan.objects.filter(addon=addon).update(
                status=amo.STATUS_DISABLED
            )
            PreloadTestPlan.objects.create(addon=addon, filename=filename)

            messages.success(
                request,
                _('Application for preload successfully submitted.'))
            return redirect(addon.get_dev_url('versions'))
        else:
            messages.error(request, _('There was an error with the form.'))
    else:
        form = PreloadTestPlanForm()

    return jingo.render(request, 'developers/apps/preload/submit.html', {
        'addon': addon,
        'form': form
    })


@dev_required
def version_edit(request, addon_id, addon, version_id):
    show_features = addon.is_packaged
    formdata = request.POST if request.method == 'POST' else None
    version = get_object_or_404(Version, pk=version_id, addon=addon)
    version.addon = addon  # Avoid extra useless query.
    form = AppVersionForm(formdata, instance=version)
    all_forms = [form]

    if show_features:
        appfeatures = version.features
        appfeatures_form = AppFeaturesForm(request.POST or None,
                                           instance=appfeatures)
        all_forms.append(appfeatures_form)

    if request.method == 'POST' and all(f.is_valid() for f in all_forms):
        [f.save() for f in all_forms]

        create_comm_note(addon, addon.current_version,
                         request.amo_user, f.data['approvalnotes'],
                         note_type=comm.REVIEWER_COMMENT)

        messages.success(request, _('Version successfully edited.'))
        return redirect(addon.get_dev_url('versions'))

    context = {
        'addon': addon,
        'version': version,
        'form': form
    }

    if show_features:
        context.update({
            'appfeatures_form': appfeatures_form,
            'appfeatures': appfeatures,
            'feature_list': [unicode(f) for f in appfeatures.to_list()]
        })

    return jingo.render(request, 'developers/apps/version_edit.html', context)


@dev_required
@post_required
def version_publicise(request, addon_id, addon):
    version_id = request.POST.get('version_id')
    version = get_object_or_404(Version, pk=version_id, addon=addon)
    if version.all_files[0].status == amo.STATUS_PUBLIC_WAITING:
        File.objects.filter(version=version).update(status=amo.STATUS_PUBLIC)
        amo.log(amo.LOG.CHANGE_VERSION_STATUS, unicode(version.status[0]),
                version)
        # Call update_version, so various other bits of data update.
        addon.update_version()

        # If the version we are publishing is the current_version one, and the
        # app was in waiting state as well, update the app status.
        if (version == addon.current_version and
                addon.status == amo.STATUS_PUBLIC_WAITING):
            addon.update(status=amo.STATUS_PUBLIC)
            amo.log(amo.LOG.CHANGE_STATUS, addon.get_status_display(), addon)

        # Call to update names and locales if changed.
        addon.update_name_from_package_manifest()
        addon.update_supported_locales()
        messages.success(request, _('Version successfully made public.'))

    return redirect(addon.get_dev_url('versions'))


@dev_required
@post_required
@transaction.commit_on_success
def version_delete(request, addon_id, addon):
    version_id = request.POST.get('version_id')
    version = get_object_or_404(Version, pk=version_id, addon=addon)
    if version.all_files[0].status == amo.STATUS_BLOCKED:
        raise PermissionDenied
    version.delete()
    messages.success(request,
                     _('Version "{0}" deleted.').format(version.version))
    return redirect(addon.get_dev_url('versions'))


@dev_required(owner_for_post=True, webapp=True)
def ownership(request, addon_id, addon, webapp=False):
    # Authors.
    qs = AddonUser.objects.filter(addon=addon).order_by('position')
    user_form = forms.AuthorFormSet(request.POST or None, queryset=qs)

    if request.method == 'POST' and user_form.is_valid():
        # Authors.
        authors = user_form.save(commit=False)
        redirect_url = addon.get_dev_url('owner')

        for author in authors:
            action = None
            if not author.id or author.user_id != author._original_user_id:
                action = amo.LOG.ADD_USER_WITH_ROLE
                author.addon = addon
            elif author.role != author._original_role:
                action = amo.LOG.CHANGE_USER_WITH_ROLE

            author.save()
            if action:
                amo.log(action, author.user, author.get_role_display(), addon)
            if (author._original_user_id and
                author.user_id != author._original_user_id):
                amo.log(amo.LOG.REMOVE_USER_WITH_ROLE,
                        (UserProfile, author._original_user_id),
                        author.get_role_display(), addon)

        for author in user_form.deleted_objects:
            if author.user_id == request.user.id:
                # The current user removed their own access to the app.
                redirect_url = reverse('mkt.developers.apps')
            amo.log(amo.LOG.REMOVE_USER_WITH_ROLE, author.user,
                    author.get_role_display(), addon)

        messages.success(request, _('Changes successfully saved.'))
        return redirect(redirect_url)

    ctx = dict(addon=addon, webapp=webapp, user_form=user_form)
    return jingo.render(request, 'developers/apps/owner.html', ctx)


@anonymous_csrf
def validate_addon(request):
    return jingo.render(request, 'developers/validate_addon.html', {
        'upload_hosted_url':
            reverse('mkt.developers.standalone_hosted_upload'),
        'upload_packaged_url':
            reverse('mkt.developers.standalone_packaged_upload'),
    })


@post_required
def _upload(request, addon=None, is_standalone=False):

    # If there is no user, default to None (saves the file upload as anon).
    form = NewPackagedAppForm(request.POST, request.FILES,
                              user=getattr(request, 'amo_user', None),
                              addon=addon)
    if form.is_valid():
        tasks.validator.delay(form.file_upload.pk)

    if addon:
        return redirect('mkt.developers.upload_detail_for_addon',
                        addon.app_slug, form.file_upload.pk)
    elif is_standalone:
        return redirect('mkt.developers.standalone_upload_detail',
                        'packaged', form.file_upload.pk)
    else:
        return redirect('mkt.developers.upload_detail',
                        form.file_upload.pk, 'json')


@login_required
def upload_new(*args, **kwargs):
    return _upload(*args, **kwargs)


@anonymous_csrf
def standalone_packaged_upload(request):
    return _upload(request, is_standalone=True)


@dev_required
def upload_for_addon(request, addon_id, addon):
    return _upload(request, addon=addon)


@dev_required
def refresh_manifest(request, addon_id, addon, webapp=False):
    log.info('Manifest %s refreshed for %s' % (addon.manifest_url, addon))
    _update_manifest(addon_id, True, {})
    return http.HttpResponse(status=204)


@post_required
@json_view
def _upload_manifest(request, is_standalone=False):
    form = forms.NewManifestForm(request.POST, is_standalone=is_standalone)
    if (not is_standalone and
        waffle.switch_is_active('webapps-unique-by-domain')):
        # Helpful error if user already submitted the same manifest.
        dup_msg = trap_duplicate(request, request.POST.get('manifest'))
        if dup_msg:
            return {'validation': {'errors': 1, 'success': False,
                    'messages': [{'type': 'error', 'message': dup_msg,
                                  'tier': 1}]}}
    if form.is_valid():
        upload = FileUpload.objects.create()
        tasks.fetch_manifest.delay(form.cleaned_data['manifest'], upload.pk)
        if is_standalone:
            return redirect('mkt.developers.standalone_upload_detail',
                            'hosted', upload.pk)
        else:
            return redirect('mkt.developers.upload_detail', upload.pk, 'json')
    else:
        error_text = _('There was an error with the submission.')
        if 'manifest' in form.errors:
            error_text = ' '.join(form.errors['manifest'])
        error_message = {'type': 'error', 'message': error_text, 'tier': 1}

        v = {'errors': 1, 'success': False, 'messages': [error_message]}
        return make_validation_result(dict(validation=v, error=error_text))


@login_required
def upload_manifest(*args, **kwargs):
    """Wrapper function for `_upload_manifest` so we can keep the
    standalone validator separate from the manifest upload stuff.

    """
    return _upload_manifest(*args, **kwargs)


def standalone_hosted_upload(request):
    return _upload_manifest(request, is_standalone=True)


@json_view
@anonymous_csrf_exempt
def standalone_upload_detail(request, type_, uuid):
    upload = get_object_or_404(FileUpload, uuid=uuid)
    url = reverse('mkt.developers.standalone_upload_detail',
                  args=[type_, uuid])
    return upload_validation_context(request, upload, url=url)


@dev_required
@json_view
def upload_detail_for_addon(request, addon_id, addon, uuid):
    upload = get_object_or_404(FileUpload, uuid=uuid)
    return json_upload_detail(request, upload, addon=addon)


def make_validation_result(data):
    """Safe wrapper around JSON dict containing a validation result."""
    if not settings.EXPOSE_VALIDATOR_TRACEBACKS:
        if data['error']:
            # Just expose the message, not the traceback.
            data['error'] = data['error'].strip().split('\n')[-1].strip()
    if data['validation']:
        for msg in data['validation']['messages']:
            for k, v in msg.items():
                msg[k] = escape_all(v)
    return data


@dev_required(allow_editors=True)
def file_validation(request, addon_id, addon, file_id):
    file = get_object_or_404(File, id=file_id)

    v = addon.get_dev_url('json_file_validation', args=[file.id])
    return jingo.render(request, 'developers/validation.html',
                        dict(validate_url=v, filename=file.filename,
                             timestamp=file.created,
                             addon=addon))


@json_view
@csrf_exempt
@dev_required(allow_editors=True)
def json_file_validation(request, addon_id, addon, file_id):
    file = get_object_or_404(File, id=file_id)
    if not file.has_been_validated:
        if request.method != 'POST':
            return http.HttpResponseNotAllowed(['POST'])

        try:
            v_result = tasks.file_validator(file.id)
        except Exception, exc:
            log.error('file_validator(%s): %s' % (file.id, exc))
            error = "\n".join(traceback.format_exception(*sys.exc_info()))
            return make_validation_result({'validation': '',
                                           'error': error})
    else:
        v_result = file.validation
    validation = json.loads(v_result.validation)

    return make_validation_result(dict(validation=validation, error=None))


@json_view
def json_upload_detail(request, upload, addon=None):
    result = upload_validation_context(request, upload, addon=addon)
    if result['validation']:
        if result['validation']['errors'] == 0:
            try:
                parse_addon(upload, addon=addon)
            except django_forms.ValidationError, exc:
                m = []
                for msg in exc.messages:
                    # Simulate a validation error so the UI displays it.
                    m.append({'type': 'error', 'message': msg, 'tier': 1})
                v = make_validation_result(dict(error='',
                                                validation=dict(messages=m)))
                return json_view.error(v)
    return result


def upload_validation_context(request, upload, addon=None, url=None):
    if not settings.VALIDATE_ADDONS:
        upload.task_error = ''
        upload.is_webapp = True
        upload.validation = json.dumps({'errors': 0, 'messages': [],
                                        'metadata': {}, 'notices': 0,
                                        'warnings': 0})
        upload.save()

    validation = json.loads(upload.validation) if upload.validation else ''
    if not url:
        if addon:
            url = reverse('mkt.developers.upload_detail_for_addon',
                          args=[addon.app_slug, upload.uuid])
        else:
            url = reverse('mkt.developers.upload_detail',
                          args=[upload.uuid, 'json'])
    report_url = reverse('mkt.developers.upload_detail', args=[upload.uuid])

    return make_validation_result(dict(upload=upload.uuid,
                                       validation=validation,
                                       error=upload.task_error, url=url,
                                       full_report_url=report_url))


def upload_detail(request, uuid, format='html'):
    upload = get_object_or_404(FileUpload, uuid=uuid)

    if format == 'json' or request.is_ajax():
        return json_upload_detail(request, upload)

    validate_url = reverse('mkt.developers.standalone_upload_detail',
                           args=['hosted', upload.uuid])
    return jingo.render(request, 'developers/validation.html',
                        dict(validate_url=validate_url, filename=upload.name,
                             timestamp=upload.created))


@dev_required(webapp=True, staff=True)
def addons_section(request, addon_id, addon, section, editable=False,
                   webapp=False):
    basic = AppFormBasic if webapp else addon_forms.AddonFormBasic
    models = {'basic': basic,
              'media': AppFormMedia,
              'details': AppFormDetails,
              'support': AppFormSupport,
              'technical': AppFormTechnical,
              'admin': forms.AdminSettingsForm}

    is_dev = acl.check_addon_ownership(request, addon, dev=True)

    if section not in models:
        raise http.Http404()

    version = addon.current_version or addon.latest_version

    tags, previews, restricted_tags = [], [], []
    cat_form = appfeatures = appfeatures_form = version_form = None
    formdata = request.POST if request.method == 'POST' else None

    # Permissions checks.
    # Only app owners can edit any of the details of their apps.
    # Users with 'Apps:Configure' can edit the admin settings.
    if (section != 'admin' and not is_dev) or (section == 'admin' and
        not acl.action_allowed(request, 'Apps', 'Configure') and
        not acl.action_allowed(request, 'Apps', 'ViewConfiguration')):
        raise PermissionDenied

    if section == 'basic':
        cat_form = CategoryForm(formdata, product=addon, request=request)
        # Only show/use the release notes form for hosted apps, packaged apps
        # can do that from the version edit page.
        if not addon.is_packaged:
            version_form = AppVersionForm(formdata, instance=version)

    elif section == 'media':
        previews = PreviewFormSet(
            request.POST or None, prefix='files',
            queryset=addon.get_previews())

    elif section == 'technical':
        # Only show/use the features form for hosted apps, packaged apps
        # can do that from the version edit page.
        if not addon.is_packaged:
            appfeatures = version.features
            appfeatures_form = AppFeaturesForm(formdata, instance=appfeatures)

    elif section == 'admin':
        tags = addon.tags.not_blacklisted().values_list('tag_text', flat=True)
        restricted_tags = addon.tags.filter(restricted=True)

    # Get the slug before the form alters it to the form data.
    valid_slug = addon.app_slug
    if editable:
        if request.method == 'POST':

            if (section == 'admin' and
                not acl.action_allowed(request, 'Apps', 'Configure')):
                raise PermissionDenied

            form = models[section](formdata, request.FILES,
                                   instance=addon, request=request)

            all_forms = [form, previews]
            for additional_form in (appfeatures_form, cat_form, version_form):
                if additional_form:
                    all_forms.append(additional_form)

            if all(not f or f.is_valid() for f in all_forms):
                if cat_form:
                    cat_form.save()

                addon = form.save(addon)

                if appfeatures_form:
                    appfeatures_form.save()

                if version_form:
                    version_form.save()

                if 'manifest_url' in form.changed_data:
                    addon.update(
                        app_domain=addon.domain_from_url(addon.manifest_url))
                    update_manifests([addon.pk])

                if previews:
                    for preview in previews.forms:
                        preview.save(addon)

                editable = False
                if section == 'media':
                    amo.log(amo.LOG.CHANGE_ICON, addon)
                else:
                    amo.log(amo.LOG.EDIT_PROPERTIES, addon)

                valid_slug = addon.app_slug
        else:
            form = models[section](instance=addon, request=request)
    else:
        form = False

    data = {'addon': addon,
            'webapp': webapp,
            'version': version,
            'form': form,
            'editable': editable,
            'tags': tags,
            'restricted_tags': restricted_tags,
            'cat_form': cat_form,
            'version_form': version_form,
            'preview_form': previews,
            'valid_slug': valid_slug, }

    if appfeatures_form and appfeatures:
        data.update({
            'appfeatures': appfeatures,
            'feature_list': [unicode(f) for f in appfeatures.to_list()],
            'appfeatures_form': appfeatures_form
        })

    return jingo.render(request,
                        'developers/apps/edit/%s.html' % section, data)


@never_cache
@dev_required(skip_submit_check=True)
@json_view
def image_status(request, addon_id, addon, icon_size=64):
    # Default icon needs no checking.
    if not addon.icon_type or addon.icon_type.split('/')[0] == 'icon':
        icons = True
    # Persona icon is handled differently.
    elif addon.type == amo.ADDON_PERSONA:
        icons = True
    else:
        icons = os.path.exists(os.path.join(addon.get_icon_dir(),
                                            '%s-%s.png' %
                                            (addon.id, icon_size)))
    previews = all(os.path.exists(p.thumbnail_path)
                   for p in addon.get_previews())
    return {'overall': icons and previews,
            'icons': icons,
            'previews': previews}


@json_view
def ajax_upload_media(request, upload_type):
    errors = []
    upload_hash = ''

    if 'upload_image' in request.FILES:
        upload_preview = request.FILES['upload_image']
        upload_preview.seek(0)
        content_type = upload_preview.content_type
        errors, upload_hash = check_upload(upload_preview, upload_type,
                                           content_type)

    else:
        errors.append(_('There was an error uploading your preview.'))

    if errors:
        upload_hash = ''

    return {'upload_hash': upload_hash, 'errors': errors}


@dev_required
def upload_media(request, addon_id, addon, upload_type):
    return ajax_upload_media(request, upload_type)


@dev_required
@post_required
def remove_locale(request, addon_id, addon):
    locale = request.POST.get('locale')
    if locale and locale != addon.default_locale:
        addon.remove_locale(locale)
        return http.HttpResponse()
    return http.HttpResponseBadRequest()


def docs(request, doc_name=None, doc_page=None):
    filename = ''

    all_docs = {'policies': ['agreement']}

    if doc_name and doc_name in all_docs:
        filename = '%s.html' % doc_name
        if doc_page and doc_page in all_docs[doc_name]:
            filename = '%s-%s.html' % (doc_name, doc_page)
        else:
            # TODO: Temporary until we have a `policies` docs index.
            filename = None

    if not filename:
        return redirect('ecosystem.landing')

    return jingo.render(request, 'developers/docs/%s' % filename)


@login_required
def terms(request):
    form = forms.DevAgreementForm({'read_dev_agreement': True},
                                  instance=request.amo_user)
    if request.POST and form.is_valid():
        form.save()
        log.info('Dev agreement agreed for user: %s' % request.amo_user.pk)
        if request.GET.get('to') and request.GET['to'].startswith('/'):
            return redirect(request.GET['to'])
        messages.success(request, _('Terms of service accepted.'))
    return jingo.render(request, 'developers/terms.html',
                        {'accepted': request.amo_user.read_dev_agreement,
                         'agreement_form': form})


@login_required
def api(request):
    roles = request.amo_user.groups.filter(name='Admins').exists()
    f = APIConsumerForm()
    if roles:
        messages.error(request,
                       _('Users with the admin role cannot use the API.'))

    elif request.method == 'POST':
        if 'delete' in request.POST:
            try:
                consumer = Access.objects.get(pk=request.POST.get('consumer'))
                consumer.delete()
            except Access.DoesNotExist:
                messages.error(request, _('No such API key.'))
        else:
            key = 'mkt:%s:%s:%s' % (
                request.amo_user.pk,
                request.amo_user.email,
                Access.objects.filter(user=request.user).count())
            access = Access.objects.create(key=key,
                                           user=request.user,
                                           secret=generate())
            f = APIConsumerForm(request.POST, instance=access)
            if f.is_valid():
                f.save()
                messages.success(request, _('New API key generated.'))
            else:
                access.delete()
    consumers = list(Access.objects.filter(user=request.user))
    return jingo.render(request, 'developers/api.html',
                        {'consumers': consumers, 'roles': roles, 'form': f})


@addon_view
@post_required
@any_permission_required([('Admin', '%'),
                          ('Apps', 'Configure')])
def blocklist(request, addon):
    """
    Blocklists the app by creating a new version/file.
    """
    if addon.status != amo.STATUS_BLOCKED:
        addon.create_blocklisted_version()
        messages.success(request, _('Created blocklisted version.'))
    else:
        messages.info(request, _('App already blocklisted.'))

    return redirect(addon.get_dev_url('versions'))


@waffle_switch('view-transactions')
@login_required
def transactions(request):
    form, transactions = _get_transactions(request)
    return jingo.render(
        request, 'developers/transactions.html',
        {'form': form,
         'CONTRIB_TYPES': amo.CONTRIB_TYPES,
         'count': transactions.count(),
         'transactions': amo.utils.paginate(request,
                                            transactions, per_page=50)})


def _get_transactions(request):
    apps = addon_listing(request, webapp=True)[0]
    transactions = Contribution.objects.filter(addon__in=list(apps),
                                               type__in=amo.CONTRIB_TYPES)

    form = TransactionFilterForm(request.GET, apps=apps)
    if form.is_valid():
        transactions = _filter_transactions(transactions, form.cleaned_data)
    return form, transactions


def _filter_transactions(qs, data):
    """Handle search filters and queries for transactions."""
    filter_mapping = {'app': 'addon_id',
                      'transaction_type': 'type',
                      'transaction_id': 'uuid',
                      'date_from': 'created__gte',
                      'date_to': 'created__lte'}
    for form_field, db_field in filter_mapping.iteritems():
        if data.get(form_field):
            try:
                qs = qs.filter(**{db_field: data[form_field]})
            except ValueError:
                continue
    return qs


def testing(request):
    return jingo.render(request, 'developers/testing.html')
