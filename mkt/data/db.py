from django.core.exceptions import ObjectDoesNotExist

import mkt
from mkt.constants.apps import INSTALL_TYPE_USER
from mkt.developers import tasks
from mkt.files.models import FileUpload
from mkt.tags.models import Tag, AddonTag
from mkt.users.models import UserProfile
from mkt.webapps.models import get_excluded_in, Webapp


class DBStore(object):
    DoesNotExist = ObjectDoesNotExist

    def apps_installed_for(self, user):
        return Webapp.objects.no_cache().filter(
            installed__user=user,
            installed__install_type=INSTALL_TYPE_USER).order_by(
                '-installed__created')

    def apps_created_by(self, user):
        return Webapp.objects.filter(authors=user)

    def user_relevant_apps(self, user):
        return {
            'developed': list(user.addonuser_set.filter(
                role=mkt.AUTHOR_ROLE_OWNER).values_list(
                    'addon_id', flat=True)),
            'installed': list(user.installed_set.values_list('addon_id',
                                                             flat=True)),
            'purchased': list(user.purchase_ids()),
        }

    def uninstall_app(self, user, pk):
        to_remove = Webapp.objects.get(pk=pk)
        installed = user.installed_set.get(
            install_type=INSTALL_TYPE_USER, addon_id=to_remove.pk)
        installed.delete()

    def get_app(self, pk=None, slug=None, region=None):
        qs = Webapp.objects
        if region is not None:
            qs = qs.exclude(id__in=get_excluded_in(region))
        if pk is not None:
            return qs.get(pk=pk)
        if slug is not None:
            return qs.get(app_slug=slug)

    def get_account(self, pk=None, email=None, uid=None):
        if len(filter(None, [pk, email, uid])) != 1:
            print "** ", pk, email, uid
            raise ObjectDoesNotExist("Pass exactly one of pk, email, or uid")
        if pk is not None:
            return UserProfile.objects.get(pk=pk)
        elif email is not None:
            return UserProfile.objects.get(email=email)
        elif uid is not None:
            return UserProfile.objects.get(fxa_uid=uid)

    def get_anonymous_account(self):
        return UserProfile(is_verified=False)

    def create_account(self, **kwargs):
        return UserProfile.objects.create(**kwargs)

    def get_upload(self, uuid):
        return FileUpload.objects.get(uuid=uuid)

    def create_app_from_upload(self, upload, user, is_packaged):
        app = Webapp.from_upload(upload, is_packaged=is_packaged)
        tasks.fetch_icon.delay(app, app.latest_version.all_files[0])

    def remove_tag(self, app, tag_text):
        tag, created = Tag.objects.get_or_create(tag_text=tag_text)
        for addon_tag in AddonTag.objects.filter(addon=app, tag=tag):
            addon_tag.delete()
        mkt.log(mkt.LOG.REMOVE_TAG, tag, app)


store = DBStore()
