from django import forms
from django.conf import settings
from django.forms import ValidationError

import happyforms
from jinja2.filters import do_filesizeformat
from django.utils.translation import ugettext as _, ugettext_lazy as _lazy

from mkt.api.forms import SluggableModelChoiceField
from mkt.comm.models import CommunicationThread
from mkt.constants import comm
from mkt.extensions.models import Extension
from mkt.webapps.models import Webapp


class AppSlugForm(happyforms.Form):
    app = SluggableModelChoiceField(queryset=Webapp.with_deleted.all(),
                                    sluggable_to_field_name='app_slug')


class ExtensionSlugForm(happyforms.Form):
    extension = SluggableModelChoiceField(queryset=Extension.objects.all(),
                                          sluggable_to_field_name='slug')


class CreateCommNoteForm(happyforms.Form):
    body = forms.CharField(
        error_messages={'required': _lazy('Note body is empty.')})
    note_type = forms.TypedChoiceField(
        empty_value=comm.NO_ACTION,
        coerce=int, choices=[(x, x) for x in comm.API_NOTE_TYPE_ALLOWED],
        error_messages={'invalid_choice': _lazy(u'Invalid note type.')})


class CreateCommThreadForm(CreateCommNoteForm):
    app = SluggableModelChoiceField(queryset=Webapp.with_deleted.all(),
                                    sluggable_to_field_name='app_slug')
    version = forms.CharField()

    def clean_version(self):
        version_num = self.cleaned_data['version']
        versions = self.cleaned_data['app'].versions.filter(
            version=version_num).order_by('-created')
        if versions.exists():
            return versions[0]
        raise forms.ValidationError(
            _('Version %s does not exist' % version_num))


class CommAttachmentForm(happyforms.Form):
    attachment = forms.FileField(label=_lazy(u'Attachment:'))
    description = forms.CharField(required=False, label=_lazy(u'Description:'))

    max_upload_size = settings.MAX_REVIEW_ATTACHMENT_UPLOAD_SIZE

    def clean(self, *args, **kwargs):
        data = super(CommAttachmentForm, self).clean(*args, **kwargs)
        attachment = data.get('attachment')
        max_size = self.max_upload_size
        if attachment and attachment.size > max_size:
            # L10n: error raised when review attachment is too large.
            exc = _('Attachment exceeds maximum size of %s.' %
                    do_filesizeformat(self.max_upload_size))
            raise ValidationError(exc)
        return data


CommAttachmentFormSet = forms.formsets.formset_factory(CommAttachmentForm)


class UnCCForm(happyforms.Form):
    pk = SluggableModelChoiceField(
        queryset=CommunicationThread.objects.all(),
        sluggable_to_field_name='id')
