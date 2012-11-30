import datetime
import json

import jingo

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect

import amo
from access import acl
from addons.models import Category
from amo.decorators import any_permission_required, write
from amo.utils import chunked
from zadmin.decorators import admin_required

import mkt
from mkt.ecosystem.tasks import refresh_mdn_cache, tutorials
from mkt.ecosystem.models import MdnCache
from mkt.webapps.models import Webapp
from mkt.webapps.tasks import update_manifests
from mkt.zadmin.models import (FeaturedApp, FeaturedAppCarrier,
                               FeaturedAppRegion)


@any_permission_required([('Admin', '%'),
                          ('FeaturedApps', '%')])
def featured_apps_admin(request):
    return jingo.render(request, 'zadmin/featuredapp.html')


@admin_required
def ecosystem(request):
    if request.method == 'POST':
        refresh_mdn_cache()
        return redirect(request.path)

    pages = MdnCache.objects.all()
    ctx = {
        'pages': pages,
        'tutorials': tutorials
    }

    return jingo.render(request, 'zadmin/ecosystem.html', ctx)


@transaction.commit_on_success
@write
@any_permission_required([('Admin', '%'),
                          ('FeaturedApps', '%')])
def featured_apps_ajax(request):
    cat = request.REQUEST.get('category', None) or None
    if cat:
        cat = int(cat)
    if request.method == 'POST':
        if not acl.action_allowed(request, 'FeaturedApps', 'Edit'):
            raise PermissionDenied
        deleteid = request.POST.get('delete', None)
        if deleteid:
            FeaturedApp.objects.filter(category__id=cat,
                                       app__id=int(deleteid)).delete()
        bits = request.POST.get('save', None)
        if bits:
            appdata = json.loads(bits)
            regions = set(appdata.get('regions', ()))
            carriers = set(appdata.get('carriers', ()))
            startdate = appdata.get('startdate', None)
            enddate = appdata.get('enddate', None)
            fa, created = FeaturedApp.objects.get_or_create(
                category_id=cat, app_id=appdata['id'])
            if created:
                FeaturedAppRegion.objects.create(
                    featured_app=fa, region=mkt.regions.WORLDWIDE.id)
            if regions or carriers:
                fa.regions.exclude(region__in=regions).delete()
                to_create = regions - set(fa.regions.filter(region__in=regions)
                                          .values_list('region', flat=True))
                excluded_regions = [e.region for e in
                                    fa.app.addonexcludedregion.all()]
                for i in to_create:
                    if i in excluded_regions:
                        continue
                    FeaturedAppRegion.objects.create(featured_app=fa, region=i)

                fa.carriers.exclude(carrier__in=carriers).delete()
                to_create = carriers - set(
                    fa.carriers.filter(carrier__in=carriers)
                    .values_list('carrier', flat=True))
                for c in to_create:
                    FeaturedAppCarrier.objects.create(featured_app=fa,
                                                      carrier=c)

            if startdate:
                fa.start_date = datetime.datetime.strptime(startdate,
                                                           '%Y-%m-%d')
            else:
                fa.start_date = None
            if enddate:
                fa.end_date = datetime.datetime.strptime(enddate,
                                                         '%Y-%m-%d')
            else:
                fa.end_date = None
            fa.save()
            return HttpResponse("saved")
    extras = json.loads(request.GET.get('extras', '[]'))
    apps_regions_carriers = [
        [UnsavedFeaturedApp(x['id'],
                            x['startdate'],
                            x['enddate']),
         x['regions'],
         [],
         x['carriers'],
         True]
        for x in extras]
    for app in FeaturedApp.objects.filter(category__id=cat):
        regions = app.regions.values_list('region', flat=True)
        excluded_regions = app.app.addonexcludedregion.values_list('region',
                                                                   flat=True)
        carriers = app.carriers.values_list('carrier', flat=True)
        apps_regions_carriers.append((app, regions, excluded_regions, carriers,
                                      False))
    return jingo.render(request, 'zadmin/featured_apps_ajax.html',
                        {'apps_regions_carriers': apps_regions_carriers,
                         'regions': mkt.regions.REGIONS_CHOICES,
                         'carriers': settings.CARRIER_URLS})


class UnsavedFeaturedApp(object):
    def __init__(self, pk, start_date, end_date):
        self.app = Webapp.objects.get(pk=pk)
        self.is_sponsor = False
        self.start_date = start_date and datetime.datetime.strptime(
            start_date, '%Y-%m-%d')
        self.end_date = end_date and datetime.datetime.strptime(
            end_date, '%Y-%m-%d')


@any_permission_required([('Admin', '%'),
                          ('FeaturedApps', '%')])
def featured_categories_ajax(request):
    cats = Category.objects.filter(type=amo.ADDON_WEBAPP)
    return jingo.render(request, 'zadmin/featured_categories_ajax.html', {
        'homecount': FeaturedApp.objects.filter(category=None).count(),
        'categories': [{
            'name': cat.name,
            'id': cat.pk,
            'count': FeaturedApp.objects.filter(category=cat).count()
        } for cat in cats]})


@admin_required(reviewers=True)
def manifest_revalidation(request):
    if request.method == 'POST':
        # collect the apps to revalidate
        qs = Q(is_packaged=False, status=amo.STATUS_PUBLIC,
               disabled_by_user=False)
        webapp_pks = Webapp.objects.filter(qs).values_list('pk', flat=True)

        for pks in chunked(webapp_pks, 100):
            update_manifests.delay(list(pks), check_hash=False)

        amo.messages.success(request, "Manifest revalidation queued")

    return jingo.render(request, 'zadmin/manifest.html')
