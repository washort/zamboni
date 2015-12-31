from django.conf import settings
from django.utils import translation

import jingo
from mock import Mock, patch
from nose.tools import eq_

from mkt.site.tests import TestCase
from mkt.translations import helpers
from mkt.translations.fields import save_signal
from mkt.translations.models import PurifiedTranslation
from mkt.translations.tests.testapp.models import TranslatedModel
from mkt.site.utils import env
from mkt.webapps.models import Webapp


def super():
    jingo.load_helpers()


def test_locale_html():
    """Test HTML attributes for languages different than the site language"""
    testfield = Mock()

    # same language: no need for attributes
    this_lang = translation.get_language()
    testfield.locale = this_lang
    s = helpers.locale_html(testfield)
    assert not s, 'no special HTML attributes for site language'

    # non-rtl language
    testfield.locale = 'de'
    s = helpers.locale_html(testfield)
    eq_(s, ' lang="de" dir="ltr"')

    # rtl language
    for lang in settings.RTL_LANGUAGES:
        testfield.locale = lang
        s = helpers.locale_html(testfield)
        eq_(s, ' lang="%s" dir="rtl"' % testfield.locale)


def test_locale_html_xss():
    """Test for nastiness-removal in the transfield's locale"""
    testfield = Mock()

    # same language: no need for attributes
    testfield.locale = '<script>alert(1)</script>'
    s = helpers.locale_html(testfield)
    assert '<script>' not in s
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in s


def test_empty_locale_html():
    """locale_html must still work if field is None."""
    s = helpers.locale_html(None)
    assert not s, 'locale_html on None must be empty.'


def test_truncate_purified_field():
    s = '<i>one</i><i>two</i>'
    t = PurifiedTranslation(localized_string=s)
    actual = env.from_string('{{ s|truncate(6) }}').render({'s': t})
    eq_(actual, s)


def test_truncate_purified_field_xss():
    """Truncating should not introduce xss issues."""
    s = 'safe <script>alert("omg")</script>'
    t = PurifiedTranslation(localized_string=s)
    actual = env.from_string('{{ s|truncate(100) }}').render({'s': t})
    eq_(actual, 'safe &lt;script&gt;alert("omg")&lt;/script&gt;')
    actual = env.from_string('{{ s|truncate(5) }}').render({'s': t})
    eq_(actual, 'safe ...')


def test_l10n_menu():
    # No remove_locale_url provided.
    menu = helpers.l10n_menu({})
    assert 'data-rm-locale=""' in menu, menu

    # Specific remove_locale_url provided (eg for user).
    menu = helpers.l10n_menu({}, remove_locale_url='/some/url/')
    assert 'data-rm-locale="/some/url/"' in menu, menu

    # Use the remove_locale_url taken from the addon in the context.
    menu = helpers.l10n_menu({'addon': Webapp()},
                             remove_locale_url='some/url/')
    assert 'data-rm-locale="/developers/app/None/rmlocale"' in menu, menu


@patch.object(settings, 'AMO_LANGUAGES', ('de', 'en-US', 'es', 'fr', 'pt-BR'))
class TestAllLocales(TestCase):
    def test_all_locales_none(self):
        addon = None
        field_name = 'description'
        eq_(helpers.all_locales(addon, field_name), None)

        addon = Mock()
        field_name = 'description'
        del addon.description
        eq_(helpers.all_locales(addon, field_name), None)

    def test_all_locales(self):
        obj = TranslatedModel()
        obj.description = {
            'en-US': 'There',
            'es': 'Is No',
            'fr': 'Spoon'
        }
        # Pretend the TranslateModel instance was saved to force Translation
        # objects to be saved.
        save_signal(sender=TranslatedModel, instance=obj)

        result = helpers.all_locales(obj, 'description')
        assert u'<div class="trans" data-name="description">' in result
        assert u'<span lang="en-us">There</span>' in result
        assert u'<span lang="es">Is No</span>' in result
        assert u'<span lang="fr">Spoon</span>' in result

    def test_all_locales_empty(self):
        obj = TranslatedModel()
        obj.description = {
            'en-US': 'There',
            'es': 'Is No',
            'fr': ''
        }
        # Pretend the TranslateModel instance was saved to force Translation
        # objects to be saved.
        save_signal(sender=TranslatedModel, instance=obj)

        result = helpers.all_locales(obj, 'description')
        assert u'<div class="trans" data-name="description">' in result
        assert u'<span lang="en-us">There</span>' in result
        assert u'<span lang="es">Is No</span>' in result
        assert u'<span lang="fr"></span>' in result

        result = helpers.all_locales(obj, 'description', prettify_empty=True)
        assert u'<div class="trans" data-name="description">' in result
        assert u'<span lang="en-us">There</span>' in result
        assert u'<span lang="es">Is No</span>' in result
        assert u'<span class="empty" lang="fr">None</span>' in result
