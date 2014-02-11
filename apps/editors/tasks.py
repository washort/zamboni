import json

from django.conf import settings

import commonware.log
from celeryutils import task
from tower import ugettext as _

import amo
import constants.editors as rvw
from addons.tasks import create_persona_preview_images
from amo.decorators import write
from amo.storage_utils import copy_stored_file, move_stored_file
from amo.utils import LocalFileStorage, send_mail_jinja
from devhub.models import ActivityLog, CommentLog, VersionLog
from editors.models import ReviewerScore
from versions.models import Version


log = commonware.log.getLogger('z.task')


@task
def add_commentlog(items, **kw):
    log.info('[%s@%s] Adding CommentLog starting with ActivityLog: %s' %
             (len(items), add_commentlog.rate_limit, items[0]))


    for al in ActivityLog.objects.filter(pk__in=items):
        # Delete existing entries:
        CommentLog.objects.filter(activity_log=al).delete()

        # Create a new entry:
        if 'comments' in al.details:
            CommentLog(comments=al.details['comments'], activity_log=al).save()


@task
def add_versionlog(items, **kw):
    log.info('[%s@%s] Adding VersionLog starting with ActivityLog: %s' %
             (len(items), add_versionlog.rate_limit, items[0]))

    for al in ActivityLog.objects.filter(pk__in=items):
        # Delete existing entries:
        VersionLog.objects.filter(activity_log=al).delete()

        for a in al.arguments:
            if isinstance(a, Version):
                vl = VersionLog(version=a, activity_log=al)
                vl.save()
                # We need to save it twice to backdate the created date.
                vl.created = al.created
                vl.save()


@task
def send_mail(cleaned_data, theme_lock):
    """
    Send emails out for respective review actions taken on themes.
    """
    theme = cleaned_data['theme']
    action = cleaned_data['action']
    comment = cleaned_data['comment']
    reject_reason = cleaned_data['reject_reason']

    reason = None
    if reject_reason:
        reason = rvw.THEME_REJECT_REASONS[reject_reason]
    elif action == rvw.ACTION_DUPLICATE:
        reason = _('Duplicate Submission')

    emails = set(theme.addon.authors.values_list('email', flat=True))
    context = {
        'theme': theme,
        'base_url': settings.SITE_URL,
        'reason': reason,
        'comment': comment
    }

    subject = None
    if action == rvw.ACTION_APPROVE:
        subject = _('Thanks for submitting your Theme')
        template = 'editors/themes/emails/approve.html'

    elif action in (rvw.ACTION_REJECT, rvw.ACTION_DUPLICATE):
        subject = _('A problem with your Theme submission')
        template = 'editors/themes/emails/reject.html'

    elif action == rvw.ACTION_FLAG:
        subject = _('Theme submission flagged for review')
        template = 'editors/themes/emails/flag_reviewer.html'

        # Send the flagged email to themes email.
        emails = [settings.THEMES_EMAIL]

    elif action == rvw.ACTION_MOREINFO:
        subject = _('A question about your Theme submission')
        template = 'editors/themes/emails/moreinfo.html'
        context['reviewer_email'] = theme_lock.reviewer.email

    send_mail_jinja(subject, template, context,
                    recipient_list=emails, from_email=settings.ADDONS_EMAIL,
                    headers={'Reply-To': settings.THEMES_EMAIL})


@task
@write
def approve_rereview(theme):
    """Replace original theme with pending theme on filesystem."""
    # If reuploaded theme, replace old theme design.
    storage = LocalFileStorage()
    rereview = theme.rereviewqueuetheme_set.all()
    reupload = rereview[0]

    if reupload.header_path != reupload.theme.header_path:
        create_persona_preview_images(
            src=reupload.header_path,
            full_dst=[
                reupload.theme.thumb_path,
                reupload.theme.icon_path],
            set_modified_on=[reupload.theme.addon])

        if not reupload.theme.is_new():
            # Legacy themes also need a preview_large.jpg.
            # Modern themes use preview.png for both thumb and preview so there
            # is no problem there.
            copy_stored_file(reupload.theme.thumb_path,
                             reupload.theme.preview_path, storage=storage)

        move_stored_file(
            reupload.header_path, reupload.theme.header_path,
            storage=storage)
    if reupload.footer_path != reupload.theme.footer_path:
        move_stored_file(
            reupload.footer_path, reupload.theme.footer_path,
            storage=storage)
    rereview.delete()

    theme.addon.increment_version()


@task
@write
def reject_rereview(theme):
    """Delete pending theme from filesystem."""
    storage = LocalFileStorage()
    rereview = theme.rereviewqueuetheme_set.all()
    reupload = rereview[0]

    storage.delete(reupload.header_path)
    storage.delete(reupload.footer_path)
    rereview.delete()
