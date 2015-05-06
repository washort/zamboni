# -*- coding: utf-8 -*-
import datetime
import hashlib
import itertools
import os
import json
import random
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from django.conf import settings

import pydenticon
import requests

import mkt
from mkt.constants.applications import DEVICE_CHOICES_IDS
from mkt.constants.base import STATUS_CHOICES_API_LOOKUP
from mkt.constants.categories import CATEGORY_CHOICES
from mkt.developers.models import AddonPaymentAccount, PaymentAccount
from mkt.developers.providers import Reference
from mkt.developers.tasks import resize_preview, save_icon
from mkt.prices.models import AddonPremium, Price
from mkt.ratings.models import Review
from mkt.ratings.tasks import addon_review_aggregates
from mkt.reviewers.models import RereviewQueue
from mkt.site.utils import app_factory, slugify, version_factory
from mkt.users.models import UserProfile
from mkt.users.utils import create_user
from mkt.webapps.models import AddonUser, AppManifest, Preview, Webapp

adjectives = [u'Exquisite', u'Delicious', u'Elegant', u'Swanky', u'Spicy',
              u'Food Truck', u'Artisanal', u'Tasty', u'Questionable', u'Drôle']
nouns = [u'Sandwich', u'Pizza', u'Curry', u'Pierogi', u'Sushi', u'Salad',
         u'Stew', u'Pasta', u'Barbeque', u'Bacon', u'Pancake', u'Waffle',
         u'Chocolate', u'Gyro', u'Cookie', u'Burrito', 'Pie', u'Crème brûlée',
         u'пельмень']
fake_app_names = list(itertools.product(adjectives, nouns))


def generate_app_data(num, skip_names=()):
    skip_names = set(skip_names)

    def _names():
        for name in fake_app_names:
            ns = u' '.join(name)
            if ns not in skip_names:
                yield ns
        repeat = 1
        while True:
            for name in fake_app_names:
                ns = u' '.join(name + (str(repeat),))
                if ns not in skip_names:
                    yield ns
            repeat += 1

    cats = itertools.cycle([c[0] for c in CATEGORY_CHOICES])
    pairs = itertools.izip(_names(), cats)
    return itertools.islice(pairs, num)

foreground = ["rgb(45,79,255)",
              "rgb(254,180,44)",
              "rgb(226,121,234)",
              "rgb(30,179,253)",
              "rgb(232,77,65)",
              "rgb(49,203,115)",
              "rgb(141,69,170)"]


def generate_icon(app):
    gen = pydenticon.Generator(8, 8, foreground=foreground)
    img = gen.generate(unicode(app.name), 128, 128,
                       output_format="png")
    save_icon(app, img)


def generate_previews(app, n=1):
    gen = pydenticon.Generator(8, 12, foreground=foreground,
                               digest=hashlib.sha512)
    for i in range(n):
        img = gen.generate(unicode(app.name) + unichr(i), 320, 480,
                           output_format="png")
        p = Preview.objects.create(addon=app, filetype="image/png",
                                   thumbtype="image/png",
                                   caption="screenshot " + str(i),
                                   position=i)
        f = tempfile.NamedTemporaryFile(suffix='.png')
        f.write(img)
        f.flush()
        resize_preview(f.name, p)


lang_prefixes = {
    'fr': u'fran\xe7ais',
    'es-ES': u'espa\xf1ol',
    'ru': u'\u0420\u0443\u0441\u0441\u043a\u0438\u0439',
    'ja': u'\u65e5\u672c\u8a9e',
    'pt-BR': u'portugu\xeas',
    'rtl': u'(RTL)',
    'en-US': u''
}


def generate_localized_names(name, langs):
    names = dict((lang, u'%s %s' % (name, lang_prefixes[lang]))
                 for lang in langs)
    return names


def generate_ratings(app, num):
    for n in range(num):
        email = 'testuser%s@example.com' % (n,)
        user, _ = UserProfile.objects.get_or_create(
            email=email, source=mkt.LOGIN_SOURCE_UNKNOWN,
            display_name=email)
        Review.objects.create(
            addon=app, user=user, rating=random.randrange(1, 6),
            title="Test Review " + str(n), body="review text")


def generate_hosted_app(name, categories, developer_name,
                        privacy_policy=None, device_types=(), status=4,
                        rated=True, uses_flash=False, default_locale='en-US',
                        **spec):
    generated_url = 'http://%s.testmanifest.com/fake-data/manifest.webapp' % (
        slugify(name),)
    a = app_factory(categories=categories, name=name, complete=False,
                    privacy_policy=spec.get('privacy_policy'),
                    file_kw={'status': status, 'uses_flash': uses_flash},
                    default_locale=default_locale, rated=rated,
                    manifest_url=spec.get('manifest_url', generated_url))
    if device_types:
        for dt in device_types:
            a.addondevicetype_set.create(device_type=DEVICE_CHOICES_IDS[dt])
    else:
        a.addondevicetype_set.create(device_type=1)
    a.versions.latest().update(reviewed=datetime.datetime.now(),
                               _developer_name=developer_name)
    if 'manifest_file' in spec:
        AppManifest.objects.create(
            version=a._latest_version,
            manifest=open(spec['manifest_file']).read())
    else:
        generate_hosted_manifest(a)
    return a


def generate_hosted_manifest(app):
    data = {
        'name': unicode(app.name),
        'description': 'This app has been automatically generated',
        'version': '1.0',
        'icons': {
            '16': 'http://testmanifest.com/icon-16.png',
            '48': 'http://testmanifest.com/icon-48.png',
            '128': 'http://testmanifest.com/icon-128.png'
        },
        'installs_allowed_from': ['*'],
        'developer': {
            'name': 'Marketplace Team',
            'url': 'https://marketplace.firefox.com/credits'
        }
    }
    AppManifest.objects.create(
        version=app._latest_version, manifest=json.dumps(data))


def generate_app_package(app, out, apptype, permissions, locale_names,
                         default_locale='en-US', version='1.0'):
    manifest = {
        'version': version.version,
        'name': unicode(app.name),
        'description': ('This packaged app has been automatically generated'
                        ' (version %s)' % (version.version,)),
        'icons': {
            '16': '/icons/16.png',
            '32': '/icons/32.png',
            '256': '/icons/256.png'
        },
        'developer': {
            'name': 'Marketplace Team',
            'url': 'https://marketplace.firefox.com/credits'
        },
        'installs_allowed_launch': ['*'],
        'from_path': 'index.html',
        'locales': dict((lang, {
            'name': name,
            'description': 'This packaged app has been automatically generated'
        }) for lang, name in generate_localized_names(
            app.name, locale_names).items()),
        'permissions': dict(((k, {"description": k})
                             for k in permissions)),
        'default_locale': default_locale,
        'orientation': 'landscape',
        'type': 'web' if apptype == 'packaged' else apptype,
        'fullscreen': 'true'
    }
    outz = ZipFile(file=out, mode='w', compression=ZIP_DEFLATED)
    try:
        for size in ('32', 'med'):
            outz.writestr(
                'icons/%s.png' % (size,),
                open(os.path.join(
                    settings.MEDIA_ROOT,
                    'img/app-icons/%s/generic.png' % (size,))).read())
        outz.writestr('script.js',
                      'document.onload=function() {alert("Hello!");};')
        outz.writestr(
            'index.html',
            '<title>Packaged app</title><script src="script.js"></script>'
            '<h1>Test packaged app</h1>')
        outz.writestr("manifest.webapp", json.dumps(manifest))
    finally:
        outz.close()
    AppManifest.objects.create(
        version=version, manifest=json.dumps(manifest))


def generate_packaged_app(name, apptype, categories, developer_name,
                          privacy_policy=None, device_types=(),
                          permissions=(), versions=None,
                          default_locale='en-US', locale_names=('en-US', 'es-ES'),
                          package_file=None, status=4, uses_flash=False, **kw):
    if versions is None:
        versions = [status]
    now = datetime.datetime.now()
    app = app_factory(categories=categories, name=name, complete=False,
                      rated=True, is_packaged=True,
                      privacy_policy=privacy_policy,
                      version_kw={
                          'version': '1.0',
                          'reviewed': now if status >= 4 else None,
                          '_developer_name': developer_name},
                      file_kw={'status': status, 'uses_flash': uses_flash})
    if device_types:
        for dt in device_types:
            app.addondevicetype_set.create(device_type=DEVICE_CHOICES_IDS[dt])
    else:
        app.addondevicetype_set.create(device_type=1)
    f = app.latest_version.all_files[0]
    f.update(filename=f.generate_filename())
    fp = os.path.join(app.latest_version.path_prefix, f.filename)
    try:
        os.makedirs(os.path.dirname(fp))
    except OSError:
        pass
    if package_file:
        return app
    with open(fp, 'w') as out:
        generate_app_package(app, out, apptype, permissions=permissions,
                             version=app.latest_version,
                             default_locale=default_locale,
                             locale_names=locale_names)
        for i, f_status in enumerate(versions[1:], 1):
            st = STATUS_CHOICES_API_LOOKUP[f_status]
            rtime = (now + datetime.timedelta(i)) if st >= 4 else None
            v = version_factory(version="1." + str(i), addon=app,
                                reviewed=rtime, created=rtime,
                                file_kw={'status': st},
                                _developer_name=developer_name)
            generate_app_package(app, out, apptype, permissions,
                                 locale_names=locale_names, version=v)
        app.update_version()
    return app


def get_or_create_payment_account(email='fakedeveloper@example.com',
                                  name='Fake App Developer'):
    user, _ = UserProfile.objects.get_or_create(
        email=email,
        source=mkt.LOGIN_SOURCE_UNKNOWN,
        display_name=name)
    try:
        acct = PaymentAccount.objects.get(user=user)
    except PaymentAccount.DoesNotExist:
        acct = Reference().account_create(
            user, {'account_name': name, 'name': name, 'email': email})
    return acct


def get_or_create_price(tier):
    return Price.objects.get_or_create(price=tier, active=True)[0]


def generate_apps(hosted=0, packaged=0, privileged=0, versions=('public',),
                  **spec_data):
    apps_data = generate_app_data(hosted + packaged + privileged)
    specs = []
    for i, (appname, cat_slug) in enumerate(apps_data):
        if i < privileged:
            spec = {'name': appname,
                    'type': 'privileged',
                    'status': versions[0],
                    'permissions': ['camera', 'storage'],
                    'categories': [cat_slug],
                    'versions': versions,
                    'num_ratings': 5,
                    'num_previews': 2}
        elif i < (privileged + packaged):
            spec = {'name': appname,
                    'type': 'packaged',
                    'status': versions[0],
                    'categories': [cat_slug],
                    'versions': versions,
                    'num_ratings': 5,
                    'num_previews': 2}
        else:
            spec = {'name': appname,
                    'type': 'hosted',
                    'status': versions[0],
                    'categories': [cat_slug],
                    'num_ratings': 5,
                    'num_previews': 2}
        spec.update(spec_data)
        specs.append(spec)

    return generate_apps_from_specs(specs, None)


GENERIC_DESCRIPTION = ""


def generate_apps_from_specs(specs, specdir, repeats=1):
    global GENERIC_DESCRIPTION
    apps = []
    specs = specs * repeats
    GENERIC_DESCRIPTION = requests.get('http://baconipsum.com/api/'
                                       '?type=meat-and-filler&paras=2'
                                       '&start-with-lorem=1').json()[0]
    existing = [unicode(w.name) for w in Webapp.with_deleted.all()]
    data = zip(specs, generate_app_data(len(specs), skip_names=existing))
    for spec, (appname, cat_slug) in data:
        spec = spec.copy()
        if spec.get('preview_files'):
            spec['preview_files'] = [os.path.join(specdir, p)
                                     for p in spec['preview_files']]
        if spec.get('package_file'):
            spec['package_file'] = os.path.join(specdir, spec['package_file'])
        if spec.get('manifest_file'):
            spec['manifest_file'] = os.path.join(specdir,
                                                 spec['manifest_file'])
        spec['name'] = spec.get('name', appname)
        spec['categories'] = spec.get('categories', [cat_slug])
        apps.append(generate_app_from_spec(**spec))
    return apps


def generate_app_from_spec(name, categories, type, status, num_previews=1,
                           num_ratings=1, locale_names=('en-US', 'es-ES'),
                           preview_files=(),
                           developer_name='Fake App Developer',
                           developer_email='fakedeveloper@example.com',
                           privacy_policy='Fake privacy policy',
                           premium_type='free', description=None,
                           default_locale='en-US', rereview=False,
                           uses_flash=False, **spec):
    status = STATUS_CHOICES_API_LOOKUP[status]
    names = generate_localized_names(name, locale_names)
    if type == 'hosted':
        import pdb; pdb.set_trace()
        app = generate_hosted_app(
            names[default_locale], categories, developer_name, status=status,
            default_locale=default_locale, **spec)
    else:
        app = generate_packaged_app(
            names[default_locale], type, categories, developer_name,
            default_locale=default_locale, status=status, **spec)
    generate_icon(app)
    if not preview_files:
        generate_previews(app, num_previews)
    if preview_files:
        for i, f in enumerate(preview_files):
            p = Preview.objects.create(addon=app, filetype="image/png",
                                       thumbtype="image/png",
                                       caption="screenshot " + str(i),
                                       position=i)
        resize_preview(f, p)
    generate_ratings(app, num_ratings)
    app.name = names
    if not description:
        description = GENERIC_DESCRIPTION
    app.description = description
    app.privacy_policy = privacy_policy
    app.support_email = developer_email
    premium_type = mkt.ADDON_PREMIUM_API_LOOKUP[premium_type]
    app.premium_type = premium_type
    app.default_locale = default_locale
    if premium_type != mkt.ADDON_FREE and status != mkt.STATUS_NULL:
        acct = get_or_create_payment_account(developer_email, developer_name)
        product_uri = Reference().product_create(acct, app)
        AddonPaymentAccount.objects.create(addon=app, payment_account=acct,
                                           account_uri=acct.uri,
                                           product_uri=product_uri)
        price = get_or_create_price(spec.get('price', '0.99'))
        AddonPremium.objects.create(addon=app, price=price)

    for optField in ('support_url', 'homepage', 'is_offline'):
        if optField in spec:
            setattr(app, optField, spec[optField])

    # Status has to be updated at the end because STATUS_DELETED apps can't
    # be saved.
    app.status = status
    app.save()
    addon_review_aggregates(app.pk)
    if rereview:
        RereviewQueue.objects.get_or_create(addon=app)
    try:
        u = UserProfile.objects.get(email=developer_email)
    except UserProfile.DoesNotExist:
        u = create_user(developer_email)
        u.display_name = developer_name
        u.save()
    AddonUser.objects.create(user=u, addon=app)
    return app
