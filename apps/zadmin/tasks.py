import collections
from datetime import datetime
import json
import logging
import os
import sys
import textwrap
import traceback

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.template import Context, Template

from celeryutils import task

from addons.models import Addon
import amo
from amo import set_user
from amo.decorators import write
from amo.helpers import absolutify
from amo.urlresolvers import reverse
from amo.utils import send_mail
from devhub.tasks import run_validator
from users.utils import get_task_user
from versions.models import Version
from zadmin.models import (EmailPreviewTopic, ValidationResult, ValidationJob,
                           ValidationJobTally)

log = logging.getLogger('z.task')


@task(rate_limit='3/s')
def admin_email(all_recipients, subject, body, preview_only=False,
                from_email=settings.DEFAULT_FROM_EMAIL,
                preview_topic='admin_email', **kw):
    log.info('[%s@%s] admin_email about %r'
             % (len(all_recipients), admin_email.rate_limit, subject))
    if preview_only:
        send = EmailPreviewTopic(topic=preview_topic).send_mail
    else:
        send = send_mail
    for recipient in all_recipients:
        send(subject, body, recipient_list=[recipient], from_email=from_email)


def tally_job_results(job_id, **kw):
    sql = """select sum(1),
                    sum(case when completed IS NOT NULL then 1 else 0 end)
             from validation_result
             where validation_job_id=%s"""
    cursor = connection.cursor()
    cursor.execute(sql, [job_id])
    total, completed = cursor.fetchone()
    if completed == total:
        # The job has finished.
        job = ValidationJob.objects.get(pk=job_id)
        job.update(completed=datetime.now())
        if job.finish_email:
            send_mail(u'Behold! Validation results for %s %s->%s'
                      % (amo.APP_IDS[job.application.id].pretty,
                         job.curr_max_version.version,
                         job.target_version.version),
                      textwrap.dedent("""
                          Aww yeah
                          %s
                          """ % absolutify(reverse('zadmin.validation'))),
                      from_email=settings.DEFAULT_FROM_EMAIL,
                      recipient_list=[job.finish_email])


@task(rate_limit='6/s')
@write
def bulk_validate_file(result_id, **kw):
    res = ValidationResult.objects.get(pk=result_id)
    task_error = None
    validation = None
    file_base = os.path.basename(res.file.file_path)
    try:
        log.info('[1@None] Validating file %s (%s) for result_id %s'
                 % (res.file, file_base, res.id))
        target = res.validation_job.target_version
        ver = {target.application.guid: [target.version]}
        # Set min/max so the validator only tests for compatibility with
        # the target version. Note that previously we explicitly checked
        # for compatibility with older versions. See bug 675306 for
        # the old behavior.
        overrides = {'targetapp_minVersion':
                                {target.application.guid: target.version},
                     'targetapp_maxVersion':
                                {target.application.guid: target.version}}
        validation = run_validator(res.file.file_path, for_appversions=ver,
                                   test_all_tiers=True, overrides=overrides)
    except:
        task_error = sys.exc_info()
        log.error(u"bulk_validate_file exception on file %s (%s): %s: %s"
                  % (res.file, file_base,
                     task_error[0], task_error[1]), exc_info=False)

    res.completed = datetime.now()
    if task_error:
        res.task_error = ''.join(traceback.format_exception(*task_error))
    else:
        res.apply_validation(validation)
        log.info('[1@None] File %s (%s) errors=%s'
                 % (res.file, file_base, res.errors))
        tally_validation_results.delay(res.validation_job.id, validation)
    res.save()
    tally_job_results(res.validation_job.id)

    if task_error:
        etype, val, tb = task_error
        raise etype, val, tb


@task
def tally_validation_results(job_id, validation_str, **kw):
    """Saves a tally of how many addons received each validation message.
    """
    validation = json.loads(validation_str)
    log.info('[@%s] tally_validation_results (job %s, %s messages)'
             % (tally_validation_results.rate_limit, job_id,
                len(validation['messages'])))
    v = ValidationJobTally(job_id)
    v.save_messages(validation['messages'])


@task
@write
def add_validation_jobs(pks, job_pk, **kw):
    log.info('[%s@None] Adding validation jobs for addons starting at: %s '
             ' for job: %s'
             % (len(pks), pks[0], job_pk))

    job = ValidationJob.objects.get(pk=job_pk)
    curr_ver = job.curr_max_version.version_int
    target_ver = job.target_version.version_int
    prelim_app = list(amo.STATUS_UNDER_REVIEW) + [amo.STATUS_BETA]
    for addon in Addon.objects.filter(pk__in=pks):
        ids = []
        base = addon.versions.filter(apps__application=job.application.id,
                                     apps__max__version_int__gte=curr_ver,
                                     apps__max__version_int__lt=target_ver)

        already_compat = addon.versions.filter(
                                    files__status=amo.STATUS_PUBLIC,
                                    apps__max__version_int__gte=target_ver)
        if already_compat.count():
            log.info('Addon %s already has a public version %r which is '
                     'compatible with target version of app %s %s (or newer)'
                     % (addon.pk, [v.pk for v in already_compat.all()],
                        job.application.id, job.target_version))
            continue

        try:
            public = (base.filter(files__status=amo.STATUS_PUBLIC)
                          .latest('id'))
        except ObjectDoesNotExist:
            public = None

        if public:
            ids.extend([f.id for f in public.files.all()])
            ids.extend(base.filter(files__status__in=prelim_app,
                                   id__gt=public.id)
                           .values_list('files__id', flat=True))

        else:
            try:
                prelim = (base.filter(files__status__in=amo.LITE_STATUSES)
                              .latest('id'))
            except ObjectDoesNotExist:
                prelim = None

            if prelim:
                ids.extend([f.id for f in prelim.files.all()])
                ids.extend(base.filter(files__status__in=prelim_app,
                                       id__gt=prelim.id)
                               .values_list('files__id', flat=True))

            else:
                ids.extend(base.filter(files__status__in=prelim_app)
                               .values_list('files__id', flat=True))

        ids = set(ids)  # Just in case.
        log.info('Adding %s files for validation for '
                 'addon: %s for job: %s' % (len(ids), addon.pk, job_pk))
        for id in set(ids):
            result = ValidationResult.objects.create(validation_job_id=job_pk,
                                                     file_id=id)
            bulk_validate_file.delay(result.pk)


def get_context(addon, version, job, results, fileob=None):
    result_links = (absolutify(reverse('devhub.bulk_compat_result',
                                       args=[addon.slug, r.pk]))
                    for r in results)
    addon_name = addon.name
    if fileob and fileob.platform.id != amo.PLATFORM_ALL.id:
        addon_name = u'%s (%s)' % (addon_name, fileob.platform)
    return Context({
            'ADDON_NAME': addon_name,
            'ADDON_VERSION': version.version,
            'APPLICATION': str(job.application),
            'COMPAT_LINK': absolutify(reverse('devhub.versions.edit',
                                              args=[addon.pk, version.pk])),
            'RESULT_LINKS': ' '.join(result_links),
            'VERSION': job.target_version.version,
        })


@task
@write
def notify_success(version_pks, job_pk, data, **kw):
    log.info('[%s@%s] Updating max version for job %s.'
             % (len(version_pks), notify_success.rate_limit, job_pk))
    job = ValidationJob.objects.get(pk=job_pk)
    set_user(get_task_user())
    dry_run = data['preview_only']
    stats = collections.defaultdict(int)
    stats['processed'] = 0
    stats['is_dry_run'] = int(dry_run)
    for version in Version.objects.filter(pk__in=version_pks):
        stats['processed'] += 1
        addon = version.addon
        file_pks = version.files.values_list('pk', flat=True)
        errors = (ValidationResult.objects.filter(validation_job=job,
                                                  file__pk__in=file_pks)
                                          .values_list('errors', flat=True))
        if any(errors):
            stats['invalid'] += 1
            log.info('Version %s for addon %s not updated, '
                     'one of the files did not pass validation'
                     % (version.pk, version.addon.pk))
            continue

        app_flag = False
        for app in version.apps.filter(
                                application=job.curr_max_version.application):
            if (app.max.version_int >= job.curr_max_version.version_int and
                app.max.version_int < job.target_version.version_int):
                stats['bumped'] += 1
                log.info('Updating version %s%s for addon %s from version %s '
                         'to version %s'
                         % (version.pk,
                            ' [DRY RUN]' if dry_run else '',
                            version.addon.pk,
                            job.curr_max_version.version,
                            job.target_version.version))
                app.max = job.target_version
                if not dry_run:
                    app.save()
                app_flag = True

            else:
                stats['missed_targets'] += 1
                log.info('Version %s for addon %s not updated, '
                         'current max version is %s not %s'
                         % (version.pk, version.addon.pk,
                            app.max.version, job.curr_max_version.version))

        if app_flag:
            results = job.result_set.filter(file__version=version)
            context = get_context(addon, version, job, results)
            for author in addon.authors.all():
                log.info(u'Emailing %s%s for addon %s, version %s about '
                         'success from bulk validation job %s'
                         % (author.email,
                            ' [PREVIEW]' if dry_run else '',
                            addon.pk, version.pk, job_pk))
                args = (Template(data['subject']).render(context),
                        Template(data['text']).render(context))
                kwargs = dict(from_email=settings.DEFAULT_FROM_EMAIL,
                              recipient_list=[author.email])
                if dry_run:
                    job.preview_success_mail(*args, **kwargs)
                else:
                    stats['author_emailed'] += 1
                    send_mail(*args, **kwargs)
                    app_id = job.target_version.application.pk
                    amo.log(amo.LOG.MAX_APPVERSION_UPDATED,
                            version.addon, version,
                            details={'version': version.version,
                                     'target': job.target_version.version,
                                     'application': app_id})
    log.info('[%s@%s] bulk update stats for job %s: {%s}'
             % (len(version_pks), notify_success.rate_limit, job_pk,
                ', '.join('%s: %s' % (k, stats[k])
                          for k in sorted(stats.keys()))))


@task
@write
def notify_failed(file_pks, job_pk, data, **kw):
    log.info('[%s@None] Notifying failed for job %s.'
             % (len(file_pks), job_pk))
    job = ValidationJob.objects.get(pk=job_pk)
    set_user(get_task_user())
    for result in ValidationResult.objects.filter(validation_job=job,
                                                  file__pk__in=file_pks):
        file = result.file
        version = file.version
        addon = version.addon
        context = get_context(addon, version, job, [result], fileob=file)
        for author in addon.authors.all():
            log.info(u'Emailing %s%s for addon %s, file %s about '
                     'error from bulk validation job %s'
                     % (author.email,
                        ' [PREVIEW]' if data['preview_only'] else '',
                        addon.pk, file.pk, job_pk))
            args = (Template(data['subject']).render(context),
                    Template(data['text']).render(context))
            kwargs = dict(from_email=settings.DEFAULT_FROM_EMAIL,
                          recipient_list=[author.email])
            if data['preview_only']:
                job.preview_failure_mail(*args, **kwargs)
            else:
                send_mail(*args, **kwargs)

        amo.log(amo.LOG.BULK_VALIDATION_EMAILED,
                addon, version,
                details={'version': version.version,
                         'file': file.filename,
                         'target': job.target_version.version})
