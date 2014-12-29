from django.core.exceptions import ObjectDoesNotExist

import mkt
from mkt.constants.apps import INSTALL_TYPE_USER
from mkt.users.models import UserProfile
from mkt.webapps.models import Webapp


## ACCOUNT API
# has accountish attributes
#has .update()
# .log_login_attempt

class DBStore(object):
    DoesNotExist = ObjectDoesNotExist

    def apps_installed_for(self, user):
        return Webapp.objects.no_cache().filter(
            installed__user=user,
            installed__install_type=INSTALL_TYPE_USER).order_by(
                '-installed__created')

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

    def get_app(self, pk=None):
            return Webapp.objects.get(pk=pk)

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

store = DBStore()
